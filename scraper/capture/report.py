"""Plain-text health report for the capture host: printed by `neet capture --status` and written to
``logs/STATUS.txt`` after every run (readable from the Google Drive app without the Mac).

A source is a *problem* once its last success is older than ``2 x cadence + 15 min``; a failure inside that window is
shown as ``retrying``, except that a run which stored only partial data (``degraded`` in its health record, e.g.
onenetwork tiles failing) is a problem at once, because the missing items would look first-seen later, and so is
any failure of a source with ``alert_on_failure`` (``failing``). Network outages are spans of the health log in which every attempt failed on DNS/connect.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .state import State
from .store import Store

RUN = "_run"                    # heartbeat record: one per `neet capture` invocation
AGENT_LABEL = "uk.ac.ncl.neet.capture"
HEARTBEAT_LATE_S = 25 * 60      # launchd ticks every 10 min
OUTAGE_MIN_S = 30 * 60
NET_ERRORS = ("NameResolution", "Failed to resolve", "ConnectTimeout", "No buffer space", "Network is unreachable",
              "ERR_NAME_NOT_RESOLVED", "ERR_INTERNET_DISCONNECTED", "ERR_NO_BUFFER_SPACE", "ERR_NETWORK_CHANGED",
              "ERR_ADDRESS_UNREACHABLE", "Page.goto: Timeout", "network down")
_HOST = re.compile(r"host='([^']+)'")


def tolerance_s(cadence_s: int) -> int:
    return 2 * cadence_s + 15 * 60


@dataclass
class SourceStatus:
    name: str
    cadence_s: int
    last_success: datetime | None
    level: str          # ok | retrying | failing | degraded | stale | never
    note: str           # why the last attempt failed ("" when it did not)

    @property
    def problem(self) -> bool:
        return self.level in ("stale", "never", "degraded", "failing")


def is_network_failure(rec: dict[str, Any]) -> bool:
    if rec.get("source") == RUN:
        return rec.get("network") == "down"
    return not rec.get("ok") and any(k in (rec.get("error") or "") for k in NET_ERRORS)


def short_error(rec: dict[str, Any]) -> str:
    """One readable phrase for a failed health record."""
    if rec.get("ok"):
        return ""
    if rec.get("degraded"):
        return "incomplete: " + "; ".join(rec["degraded"])[:80]
    if rec.get("skipped"):
        return f"skipped: {rec['skipped']} {','.join(rec.get('missing') or [])}".strip()
    err = rec.get("error") or ""
    m = _HOST.search(err)
    host = f" ({m.group(1)})" if m else ""
    if "NameResolution" in err or "Failed to resolve" in err or "ERR_NAME_NOT_RESOLVED" in err:
        return "DNS lookup failed" + host
    if "No buffer space" in err or "ERR_NO_BUFFER_SPACE" in err:
        return "network unusable (no buffer space)" + host
    if "ConnectTimeout" in err:
        return "connect timed out" + host
    if "ReadTimeout" in err or "TimeoutError" in err or "Timeout " in err:
        return "timed out" + host
    if err:
        return err.splitlines()[0][:70]
    bad = sorted({s for s in (rec.get("statuses") or []) if s is not None and not 200 <= int(s) < 300})
    if bad:
        return "HTTP " + ",".join(str(s) for s in bad)
    if rec.get("write_errors"):
        return "could not write payload"
    return "failed"


def source_statuses(store: Store, sources: Iterable, now: datetime | None = None) -> list[SourceStatus]:
    now = now or datetime.now(timezone.utc)
    last = store.last_health()
    out = []
    for src in sources:
        st = State(store.state_dir, src.name)
        ls = st.last_success()
        rec = last.get(src.name, {})
        if ls is None:
            level = "never"
        elif (now - ls).total_seconds() > tolerance_s(src.cadence_s):
            level = "stale"
        elif st.fail_streak():
            level = "degraded" if rec.get("degraded") else ("failing" if getattr(src, "alert_on_failure", False) else "retrying")
        else:
            level = "ok"
        out.append(SourceStatus(src.name, src.cadence_s, ls, level, short_error(rec) if rec else ""))
    return out


def recent_health(store: Store, since: datetime, tail_bytes: int = 8 << 20) -> list[dict[str, Any]]:
    """Health records at or after ``since``, read from the tail of the log only."""
    p = store.logs_dir / "health.jsonl"
    if not p.exists():
        return []
    with p.open("rb") as f:
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - tail_bytes))
        data = f.read()
    lines = data.splitlines()
    if size > tail_bytes:
        lines = lines[1:]  # first line is probably cut
    cut = since.isoformat()
    out = []
    for line in lines:
        try:
            r = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if r.get("at", "") >= cut:
            out.append(r)
    return out


def _proves_network(rec: dict[str, Any]) -> bool:
    """True if the record shows the internet was reachable: an HTTP status came back, or items were captured.
    (An "ok" with nothing fetched proves nothing; onenetwork used to report one during outages.)"""
    if rec.get("source") == RUN:
        return rec.get("network") == "ok"
    return any(s is not None for s in rec.get("statuses") or []) or bool(rec.get("ok") and rec.get("n_items"))


def outages(records: list[dict[str, Any]], min_s: int = OUTAGE_MIN_S) -> list[tuple[datetime, datetime, bool]]:
    """(start, end, ongoing) spans in which every attempt failed on the network, at least ``min_s`` long."""
    spans: list[tuple[datetime, datetime, bool]] = []
    start = end = None
    for r in sorted(records, key=lambda r: r.get("at", "")):
        at = datetime.fromisoformat(r["at"])
        if is_network_failure(r):
            start = start or at
            end = at
        elif start is not None and _proves_network(r):
            if (end - start).total_seconds() >= min_s:
                spans.append((start, at, False))
            start = end = None
    if start is not None and (end - start).total_seconds() >= min_s:
        spans.append((start, end, True))
    return spans


def problems(statuses: list[SourceStatus], records: list[dict[str, Any]]) -> list[str]:
    """Names of current problems, used for the headline and for change-of-state notifications."""
    out = [s.name for s in statuses if s.problem]
    runs = [r for r in records if r.get("source") == RUN]
    if runs and runs[-1].get("network") == "down":
        out.insert(0, "network")
    return out


# -- formatting ----------------------------------------------------------------
def _ago(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "never"
    return _dur((now - dt).total_seconds()) + " ago"


def _dur(s: float) -> str:
    s = max(0, int(s))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _every(cadence_s: int) -> str:
    return f"{cadence_s // 86400}d" if cadence_s % 86400 == 0 else (
        f"{cadence_s // 3600}h" if cadence_s % 3600 == 0 else f"{cadence_s // 60}m")


def _local(dt: datetime) -> str:
    return dt.astimezone().strftime("%a %d %b %H:%M")


def agent_info() -> list[str]:
    """Scheduler and machine lines (macOS only; empty elsewhere)."""
    if sys.platform != "darwin":
        return []
    lines = []
    try:
        out = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{AGENT_LABEL}"], capture_output=True,
                             text=True, timeout=10).stdout
        if out:
            def field(k: str) -> str:
                m = re.search(rf"^\t{k} = (.+)$", out, re.M)
                return m.group(1).strip() if m else "?"
            lines.append(f"Scheduler   loaded ({AGENT_LABEL}): {field('state')}, last exit {field('last exit code')}, "
                         f"{field('runs')} runs since login")
        else:
            lines.append(f"Scheduler   NOT LOADED: run ~/neet/deploy/install_launchd.sh")
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"sec = (\d+)", out)
        if m:
            boot = datetime.fromtimestamp(int(m.group(1)), timezone.utc)
            lines.append(f"Mac         up {_dur((datetime.now(timezone.utc) - boot).total_seconds())} "
                         f"(started {_local(boot)})")
    except (OSError, subprocess.SubprocessError):
        pass
    return lines


def build_report(store: Store, sources: Iterable, now: datetime | None = None, extra: list[str] | None = None,
                 days: int = 7) -> str:
    now = now or datetime.now(timezone.utc)
    statuses = source_statuses(store, sources, now)
    records = recent_health(store, now - timedelta(days=days))
    probs = problems(statuses, records)
    runs = [r for r in records if r.get("source") == RUN]
    last_any = max((datetime.fromisoformat(r["at"]) for r in records), default=None)
    last_run = datetime.fromisoformat(runs[-1]["at"]) if runs else last_any
    late = last_run is None or (now - last_run).total_seconds() > HEARTBEAT_LATE_S
    if late:
        probs.insert(0, "not running")

    title = f"NEET capture status, {_local(now)}"
    lines = [title, "=" * len(title)]
    lines.append("ALL GOOD" if not probs else f"{len(probs)} PROBLEM{'S' if len(probs) > 1 else ''}: {', '.join(probs)}")
    lines.append("")
    lines += extra or []
    run_line = f"Last run    {_ago(last_run, now)}"
    if runs:
        run_line += f": network {runs[-1].get('network', '?')}"
        if runs[-1].get("network") == "down":
            run_line += f" ({runs[-1].get('error', '')[:80]})"
    if late:
        run_line += "  <- capture is not running (Mac asleep, logged out, or agent stopped)"
    lines.append(run_line)
    try:
        free = shutil.disk_usage(Path.home()).free / 1e9
        lines.append(f"Data        {store.root}  ({free:.0f} GB free locally)")
    except OSError:
        lines.append(f"Data        {store.root}")
    lines.append("")
    lines.append(f"{'SOURCE':22s} {'EVERY':>5s}  {'LAST SUCCESS':14s} {'STATE':9s} LAST PROBLEM")
    for s in statuses:
        state = s.level.upper() if s.problem else s.level
        note = s.note if s.level != "ok" else ""
        lines.append(f"{s.name:22s} {_every(s.cadence_s):>5s}  {_ago(s.last_success, now):14s} {state:9s} {note}".rstrip())
    lines.append("")
    spans = outages(records)
    lines.append(f"Network outages, last {days} days (every request failing for {OUTAGE_MIN_S // 60}+ min):")
    if not spans:
        lines.append("  none")
    for a, b, ongoing in spans:
        lines.append(f"  {_local(a)} -> {'NOW (ongoing)' if ongoing else _local(b)}  ({_dur((b - a).total_seconds())})")
    lines.append("")
    lines.append(f"Health log  {store.logs_dir / 'health.jsonl'}")
    lines.append("Run log     ~/Library/Logs/neet-capture.out.log  (crashes: neet-capture.err.log)")
    return "\n".join(lines) + "\n"

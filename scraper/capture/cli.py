"""`neet capture` — run due capture sources once (intended to be invoked every 10 min by launchd/cron).

    neet capture                      run all due sources
    neet capture --sources rss ea     run only these (ignores cadence)
    neet capture --force              ignore cadence for all
    neet capture --status             health report (also written to <data_root>/logs/STATUS.txt after every run)
    neet capture --data-root PATH     override capture.data_root / $NEET_DATA_ROOT
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import requests

from .base import Context, env_snapshot
from .registry import all_sources
from .report import RUN, agent_info, build_report, problems, recent_health, source_statuses
from .state import State
from .store import Store, atomic_write_bytes, utcnow

logger = logging.getLogger("neet.capture")

PREFLIGHT_HOSTS = ("www.gov.uk", "www.bbc.co.uk", "one.one.one.one")
# local (not on Drive): remembers that the data root was missing on the previous run, so a transient
# not-yet-mounted Drive at login does not raise a notification but a persistent one does
NO_ROOT_MARKER = Path.home() / "Library" / "Logs" / "neet-capture.no-data-root"


def _data_root(cfg: dict, override: str | None) -> Path:
    root = override or os.environ.get("NEET_DATA_ROOT") or (cfg.get("data_root")) or "data/capture_root"
    return Path(root).expanduser()



def _network_problem(hosts=PREFLIGHT_HOSTS, timeout: float = 5.0) -> str | None:
    """None if a TCP connection to any well-known host on 443 succeeds (DNS + routing work), else the reasons.
    Without this, an outage marks every due source as failed and the daily ones wait a day to retry."""
    errs = []
    for h in hosts:
        try:
            socket.create_connection((h, 443), timeout=timeout).close()
            return None
        except OSError as exc:
            errs.append(f"{h}: {exc}")
    return "; ".join(errs) or "no preflight hosts"


def notify(title: str, message: str) -> None:
    """macOS notification (best effort; the first one may need Script Editor notifications allowed)."""
    if sys.platform != "darwin":
        return
    script = f"display notification {json.dumps(message, ensure_ascii=False)} with title {json.dumps(title, ensure_ascii=False)}"
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def _alert_on_change(store: Store, probs: list[str], notes: dict[str, str], enabled: bool) -> None:
    """Notify when a new problem appears or when everything has recovered; stay quiet while nothing changes."""
    path = store.state_dir / "_alerts.json"
    try:
        before = set(json.loads(path.read_text()).get("problems", []))
    except (OSError, ValueError):
        before = set()
    now = set(probs)
    if now != before:
        atomic_write_bytes(path, json.dumps({"problems": sorted(now), "at": utcnow().isoformat()}).encode())
    if not enabled:
        return
    new = [p for p in probs if p not in before]
    if new:
        detail = "; ".join(f"{p}: {notes[p]}" if notes.get(p) else p for p in new)
        notify("NEET capture problem", f"{detail}. Run: neet capture --status")
    elif before and not now:
        notify("NEET capture recovered", "All sources are capturing again.")


def _write_status(store: Store, sources, enabled_alerts: bool) -> None:
    """STATUS.txt for remote checks + change-of-state notification. Never fails the run."""
    try:
        atomic_write_bytes(store.logs_dir / "STATUS.txt", build_report(store, sources).encode("utf-8"))
        statuses = source_statuses(store, sources)
        records = recent_health(store, utcnow() - timedelta(hours=1))
        notes = {s.name: s.note for s in statuses}
        if records and records[-1].get("source") == RUN and records[-1].get("network") == "down":
            notes["network"] = "no internet connection"
        _alert_on_change(store, problems(statuses, records), notes, enabled_alerts)
    except Exception:
        logger.exception("could not write status report")


def _lock_holder_alive(lock) -> bool:
    """A lock names the PID that took it; the lock is live only while that process exists.
    (An interrupted download otherwise blocked its source for max(6 h, 3 x cadence).)"""
    try:
        pid = int((lock.read_text() or "0").strip() or 0)
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="neet capture")
    ap.add_argument("--config", default=None)
    ap.add_argument("--sources", nargs="+", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    from scraper.config import load as load_config
    cfg_all = load_config(args.config)
    cfg = cfg_all.get("capture", {}) or {}
    root = _data_root(cfg, args.data_root)
    alerts = bool(cfg.get("notify", True))
    if not root.exists() and not root.parent.exists():
        # Google Drive not mounted (yet): creating the path would put the data on local disk, outside Drive
        print(f"data root {root} is not available (Google Drive not mounted?); nothing captured", file=sys.stderr)
        if NO_ROOT_MARKER.exists():
            if alerts and NO_ROOT_MARKER.read_text() != "notified":
                notify("NEET capture problem", "Google Drive folder is not available; nothing is being captured.")
                NO_ROOT_MARKER.write_text("notified")
        else:
            NO_ROOT_MARKER.parent.mkdir(parents=True, exist_ok=True)
            NO_ROOT_MARKER.write_text("seen")
        return 1
    NO_ROOT_MARKER.unlink(missing_ok=True)
    store = Store(root)
    log_path = store.logs_dir / "capture.log"
    # force: scraper.runner configures logging at import, which would otherwise make this a no-op
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(log_path), logging.StreamHandler(sys.stdout)], force=True)
    sources = all_sources(cfg)

    if args.status:
        print(build_report(store, sources, extra=agent_info()), end="")
        return 0

    net = _network_problem(tuple(cfg.get("preflight_hosts") or PREFLIGHT_HOSTS))
    if net:
        # leave every source's schedule alone so each runs as soon as the network is back
        logger.warning("network down, skipping this run: %s", net)
        store.health({"source": RUN, "ok": False, "network": "down", "error": f"network down: {net}"[:300]})
        _write_status(store, sources, alerts)
        return 0

    env = env_snapshot()
    session = requests.Session()
    n_ok = n_err = 0
    for src in sources:
        if args.sources and src.name not in args.sources:
            continue
        state = State(store.state_dir, src.name)
        if not args.force and not args.sources and not state.due(src.cadence_s):
            continue
        missing = src.missing_env(env)
        if missing:
            store.health({"source": src.name, "ok": False, "skipped": "missing_env", "missing": missing})
            continue
        # per-source lock: a long download (e.g. Street Manager archives) must not block the other sources
        lock = store.state_dir / f".lock_{src.name}"
        if lock.exists():
            age = time.time() - lock.stat().st_mtime
            if not _lock_holder_alive(lock):
                logger.info("%s: stale lock (holder gone, age %.0fs); removing", src.name, age)
                lock.unlink(missing_ok=True)
        if lock.exists():
            if age < max(6 * 3600, 3 * src.cadence_s):
                logger.info("%s: previous run still active (lock age %.0fs); skipping", src.name, age)
                continue
            lock.unlink(missing_ok=True)  # stale
        lock.write_text(str(os.getpid()))
        t0 = time.time()
        ctx = Context(store=store, state=state, config=src.cfg, env=env, session=session)
        try:
            payloads = src.fetch(ctx)
            n_items = 0
            statuses = []
            n_written = n_unchanged = 0
            write_errors: list[str] = []
            for p in payloads:
                if p is None:
                    continue
                try:  # one payload that cannot be written must not lose the rest of the run
                    if store.write(src.name, p) is None:
                        n_unchanged += 1
                    else:
                        n_written += 1
                except OSError as exc:
                    logger.exception("source %s: could not write payload %s", src.name, p.tag)
                    write_errors.append(f"{p.tag}: {type(exc).__name__}: {exc}"[:200])
                    continue
                n_items += p.n_items or 0
                statuses.append(p.meta.get("status"))
            ok = (all((s is None) or (200 <= int(s) < 300) for s in statuses) if statuses else True) and not write_errors
            state.mark_run(ok=ok); state.save()
            rec = {"source": src.name, "ok": ok, "n_payloads": len(payloads), "n_written": n_written,
                   "n_unchanged": n_unchanged, "n_items": n_items,
                   "duration_s": round(time.time() - t0, 1), "statuses": statuses[:20]}
            if write_errors:
                rec.update({"n_write_errors": len(write_errors), "write_errors": write_errors[:3]})
            store.health(rec)
            n_ok += 1
        except Exception as exc:
            logger.exception("source %s failed", src.name)
            state.mark_run(ok=False); state.save()
            store.health({"source": src.name, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300],
                          "duration_s": round(time.time() - t0, 1)})
            n_err += 1
        finally:
            lock.unlink(missing_ok=True)
    logger.info("capture run done: %d ok, %d failed", n_ok, n_err)
    store.health({"source": RUN, "ok": n_err == 0, "network": "ok", "n_ok": n_ok, "n_failed": n_err})
    _write_status(store, sources, alerts)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

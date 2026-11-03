"""Stage 1: every eligible evidence item, rendered in CURB's columns plus provenance (01_pool/pool.csv).

Items are `item_key` lineages from `neet feeds build` (see scraper/feeds/lineage.py); the version rendered is the
earliest one captured. An item is in scope when it belongs to a slice (domain x city, below), eligible when it was
first captured inside the window, and then removed by the exclusions in this order, each counted:

  curb          one of CURB's 30 evidence items (same evidence text, or same domain, date_time and location)
  dev           its lineage contains one of the 40 development record_ids (official_sample.json)
  baseline      first seen only because coverage was widened (a new onenetwork tile's first capture)
  street_manager  onenetwork works whose works_ref has a Street Manager permit event before the window start
  not_renderable  CURB's formatter cannot render it (no road name or description, page HTML missing, ...)

Crime is per month: a Newcastle record is eligible only when its month was first captured inside the window;
Durham months already captured are eligible (`durham_crime_all_captured`, Oliver 3 Nov, pending Ellis)."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

from scraper.capture.sources.travel_updates import parse_updates
from scraper.capture.store import read_payload

from . import render

CURB_COLUMNS = ["item_id", "domain", "evidence_text", "source_name", "source_page", "source_url", "date_time", "location",
                "claim_text", "label", "claim_type"]
PROVENANCE = ["city", "record_id", "item_key", "capture_file", "first_seen", "valid_from", "valid_to"]
STRATUM = ["model_run", "month", "works_ref", "notice_type"]
POOL_COLUMNS = CURB_COLUMNS + PROVENANCE + STRATUM
EXCLUSIONS = ("curb", "dev", "baseline", "street_manager", "not_renderable")
SOURCES = ("onenetwork", "travel_updates", "metoffice_hourly", "police")
ROADWORKS_PUB = {"Newcastle City Council": "newcastle", "Durham": "durham"}


@dataclass
class Settings:
    window_start: str                         # ISO UTC; OPEN (Oliver): freeze commit or v4.4 tag
    window_end: Optional[str] = None          # ISO UTC, exclusive; for mock windows
    durham_crime_all_captured: bool = True    # Oliver 3 Nov, pending Ellis
    street_manager: bool = True               # Oliver 3 Nov
    durham_roadworks_url: str = "https://one.network/"   # OPEN: CURB has no Durham page
    transport_max_chars: Optional[int] = None


def slice_of(rec: dict) -> Optional[tuple[str, str]]:
    """(domain, city) of a record, or None when it is outside every held-out slice."""
    src, s, tag = rec["source"], rec["structured"], str((rec.get("provenance") or {}).get("tag") or "")
    if src == "onenetwork":
        city = ROADWORKS_PUB.get(s.get("pub_name"))
        return ("roadworks", city) if city else None
    if src == "travel_updates":
        return ("transport", "newcastle")                 # 20 Oct brief: no filter
    if src == "metoffice_hourly":
        return {"hourly_newcastle": ("weather", "newcastle"), "hourly_durham": ("weather", "durham")}.get(tag)
    if src == "police":
        if tag.startswith("newcastle_centre_"):
            return ("crime", "newcastle")
        if tag.startswith("durham_"):
            return ("crime", "durham")
    return None


def iter_records(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def load_curb(path: Path) -> tuple[set[str], set[tuple[str, str, str]]]:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    return ({norm(r["evidence_text"]) for r in rows}, {(r["domain"], r["date_time"], norm(r["location"])) for r in rows})


def load_dev(path: Path) -> set[str]:
    return {d["record_id"] for d in json.loads(path.read_text(encoding="utf-8"))}


_SM_NAME = re.compile(r"^(?:.*/)?(.+)-\d+_[a-z0-9_]+?_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})")


def street_manager_first_events(root: Path, refs: set[str], report: Optional[dict] = None) -> dict[str, str]:
    """Earliest Street Manager permit event per works reference, read from the monthly permit archives' member names
    (`<permit_ref>_<event>_<YYYY-MM-DD_HH-MM-SS>...json`; the works reference is the permit reference without its
    `-NN` suffix). Unreadable archives are skipped and listed in `report["unreadable"]`."""
    first: dict[str, str] = {}
    for z in sorted((root / "capture" / "streetmanager_archive").glob("*/*_permit_*.zip")):
        try:
            names = zipfile.ZipFile(z).namelist()
        except (zipfile.BadZipFile, OSError):
            if report is not None:
                report.setdefault("unreadable", []).append(z.name)
            continue
        for name in names:
            m = _SM_NAME.match(name)
            if not m or m.group(1) not in refs:
                continue
            at = f"{m.group(2)}T{m.group(3)}:{m.group(4)}:{m.group(5)}Z"
            if m.group(1) not in first or at < first[m.group(1)]:
                first[m.group(1)] = at
    return first


def _page_parts(root: Path, rec: dict, cache: dict) -> Optional[dict]:
    """The notice's heading, dates and body lines from the page HTML stored with its updates payload."""
    upd = Path((rec.get("provenance") or {}).get("path") or "")
    if not upd.name:
        return None
    stem = upd.name.split("_", 1)[0]
    candidates = [root / upd.parent / f"{stem}_page.html", root / upd.parent / f"{stem}_page.html.gz"]
    page = next((c for c in candidates if c.exists()), None)
    if page is None:
        return None
    if page not in cache:
        cache.clear()   # captures are visited in no particular order; keep one page parsed at a time
        cache[page] = parse_updates(read_payload(page).decode("utf-8", errors="replace"))
    s = rec["structured"]
    for u in cache[page]:
        if u["title"] == s.get("title") and u["body"] == s.get("body"):
            return render.notice_lines(u["html"])
    return None


def build_pool(records_path: Path, root: Path, settings: Settings, curb_path: Path, dev_path: Path) -> tuple[list[dict], dict]:
    start, end = settings.window_start, settings.window_end
    dev_ids = load_dev(dev_path)
    curb_text, curb_keys = load_curb(curb_path)

    # one pass: the earliest version of every in-scope item, and what the exclusions need about its lineage
    first: dict[str, dict] = {}
    slice_: dict[str, tuple[str, str]] = {}
    dev_items: set[str] = set()
    baseline_items: set[str] = set()
    works_refs: dict[str, set[str]] = defaultdict(set)
    month_first: dict[tuple[str, str], str] = {}
    month_last: dict[tuple[str, str], str] = {}
    in_scope: dict[str, str] = {}

    def in_window(at: Optional[str]) -> bool:
        return bool(at) and at >= start and (end is None or at < end)

    for rec in iter_records(records_path):
        if rec["source"] not in SOURCES:
            continue
        key = rec.get("item_key") or rec["record_id"]
        if rec["record_id"] in dev_ids:
            dev_items.add(key)
        sl = slice_of(rec)
        if sl is None:
            continue
        in_scope[key] = "/".join(sl)
        if sl[0] != "crime" and not in_window(rec.get("first_seen")):
            continue                                     # first_seen is per item, so no version of it can be eligible
        slice_[key] = sl
        if rec.get("baseline"):
            baseline_items.add(key)
        if rec["source"] == "onenetwork" and rec["structured"].get("works_ref"):
            works_refs[key].add(str(rec["structured"]["works_ref"]))
        if rec["source"] == "police":
            mk = (sl[1], str(rec["structured"].get("month")))
            at = rec.get("fetched_at") or ""
            month_first[mk] = min(month_first.get(mk, at), at)
            month_last[mk] = max(month_last.get(mk, at), at)
        cur = first.get(key)
        if cur is None or (rec.get("fetched_at") or "", rec["record_id"]) < (cur.get("fetched_at") or "", cur["record_id"]):
            first[key] = rec

    eligible: list[str] = []
    for key, rec in first.items():
        domain, city = slice_[key]
        if domain == "crime":
            mk = (city, str(rec["structured"].get("month")))
            if city == "durham" and settings.durham_crime_all_captured:
                ok = end is None or month_first[mk] < end
            elif city == "durham":
                ok = in_window(month_last[mk])           # the 20 Oct rule: the month was served inside the window
            else:
                ok = in_window(month_first[mk])
        else:
            ok = in_window(rec.get("first_seen"))
        if ok:
            eligible.append(key)

    sm_report: dict = {}
    sm_first: dict[str, str] = {}
    if settings.street_manager:
        refs = set().union(*(works_refs[k] for k in eligible if k in works_refs)) if eligible else set()
        sm_first = street_manager_first_events(root, refs, sm_report)

    counts = {"in_scope": Counter(), "eligible": Counter(), **{e: Counter() for e in EXCLUSIONS}, "pool": Counter()}
    for stratum in in_scope.values():
        counts["in_scope"][stratum] += 1
    rows: list[dict] = []
    page_cache: dict = {}
    for key in sorted(eligible):
        rec = first[key]
        domain, city = slice_[key]
        stratum = f"{domain}/{city}"
        counts["eligible"][stratum] += 1
        if domain == "roadworks":
            row = render.roadworks(rec, city, settings.durham_roadworks_url)
        elif domain == "crime":
            row = render.crime(rec, city)
        elif domain == "weather":
            row = render.weather(rec, city)
        else:
            row = render.transport(rec, _page_parts(root, rec, page_cache), settings.transport_max_chars)
        reason = None
        if row and (norm(row["evidence_text"]) in curb_text or (domain, row["date_time"], norm(row["location"])) in curb_keys):
            reason = "curb"
        elif key in dev_items:
            reason = "dev"
        elif key in baseline_items:
            reason = "baseline"
        elif any(sm_first.get(w, "9999") < start for w in works_refs.get(key, ())):
            reason = "street_manager"
        elif row is None:
            reason = "not_renderable"
        if reason:
            counts[reason][stratum] += 1
            continue
        s = rec["structured"]
        rows.append({**{c: "" for c in POOL_COLUMNS}, **row, "domain": domain, "city": city, "record_id": rec["record_id"],
                     "item_key": key, "capture_file": (rec.get("provenance") or {}).get("path") or "",
                     "first_seen": rec.get("first_seen") or "", "valid_from": rec.get("valid_from") or "",
                     "valid_to": rec.get("valid_to") or "", "model_run": s.get("modelRunDate") or "" if domain == "weather" else "",
                     "month": s.get("month") or "" if domain == "crime" else "",
                     "works_ref": ";".join(sorted(works_refs.get(key, ()))),
                     "notice_type": (s.get("hints") or {}).get("data-types") or "" if domain == "transport" else ""})
        counts["pool"][stratum] += 1
    report = {"settings": asdict(settings), "counts": {k: dict(sorted(v.items())) for k, v in counts.items()},
              "street_manager": {"refs_checked": len(set().union(*(works_refs[k] for k in eligible if k in works_refs))) if eligible else 0,
                                 "refs_with_events": len(sm_first), **sm_report}}
    return rows, report


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_csv(path: Path, rows: Iterable[dict], columns: list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r); n += 1
    return n

"""Build evidence records from the immutable capture store.

    neet feeds build --root <data_root> [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--source rss …] --out records.jsonl
    neet feeds report --root <data_root> [--since …]

Walks `<root>/capture/<source>/<day>/<time>_<tag>.<ext>` with its `.meta.json`, dispatches to the adapter of the
source, resolves Bluesky `dupes` against sibling payloads of the same run, and writes one JSON line per record
(sorted, deterministic). Continuity under hash de-dup: for every (source, tag) the *last written* payload is the
one still in force, so `report` also lists the most recent capture per (source, tag).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .base import adapters
from .model import EvidenceRecord


def iter_payloads(root: Path, sources: Optional[set[str]] = None, since: Optional[str] = None, until: Optional[str] = None) -> Iterator[tuple[Path, dict]]:
    cap = root / "capture"
    if not cap.is_dir():
        return
    for src_dir in sorted(p for p in cap.iterdir() if p.is_dir()):
        if sources and src_dir.name not in sources:
            continue
        for day_dir in sorted(p for p in src_dir.iterdir() if p.is_dir()):
            day = day_dir.name
            if since and day < since:
                continue
            if until and day > until:
                continue
            for meta_path in sorted(day_dir.glob("*.meta.json")):
                payload = Path(str(meta_path)[: -len(".meta.json")])
                if not payload.exists():
                    continue
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                meta.setdefault("source", src_dir.name)
                meta.setdefault("file", str(payload.relative_to(root)))
                yield payload, meta


def build_records(root: Path, sources: Optional[set[str]] = None, since: Optional[str] = None, until: Optional[str] = None,
                  errors: Optional[list] = None) -> list[EvidenceRecord]:
    reg = adapters()
    out: list[EvidenceRecord] = []
    by_uri: dict[str, EvidenceRecord] = {}
    pending_dupes: list[tuple[str, dict]] = []
    for payload, meta in iter_payloads(root, sources, since, until):
        adapter = reg.get(meta["source"])
        if adapter is None or not adapter.accepts(meta):
            continue
        try:
            recs = adapter.records(payload.read_bytes(), meta)
        except Exception as e:  # one bad payload must not sink the build
            if errors is not None:
                errors.append({"file": meta.get("file"), "error": repr(e)})
            continue
        for r in recs:
            if r.source == "bluesky" and r.native_id:
                if r.native_id in by_uri:          # same post captured by another strategy in this window
                    prev = by_uri[r.native_id]
                    prev.structured["strategies"] = sorted(set(prev.structured.get("strategies") or []) | set(r.structured.get("strategies") or []))
                    continue
                by_uri[r.native_id] = r
            out.append(r)
        if meta["source"] == "bluesky":
            for uri in (recs[0].provenance.get("dupes") if recs else []) or []:
                pending_dupes.append((uri, meta))
    # dupes whose record was never seen are reported, not invented
    if errors is not None:
        for uri, meta in pending_dupes:
            if uri not in by_uri:
                errors.append({"file": meta.get("file"), "error": f"unresolved dupe {uri}"})
    out.sort(key=lambda r: (r.source, r.valid_from or "", r.record_id))
    return out


def write_jsonl(records: Iterable[EvidenceRecord], out_path: Path) -> int:
    n = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


def report(records: list[EvidenceRecord]) -> str:
    lines = [f"{'source':22s} {'records':>8s} {'text':>6s} {'geom':>6s} {'valid':>6s} {'domains'}"]
    by_src: dict[str, list[EvidenceRecord]] = defaultdict(list)
    for r in records:
        by_src[r.source].append(r)
    for src, rs in sorted(by_src.items()):
        n = len(rs)
        pct = lambda f: f"{100 * sum(1 for r in rs if f(r)) / n:5.0f}%" if n else "   -"
        doms = Counter(r.domain for r in rs).most_common(3)
        lines.append(f"{src:22s} {n:8d} {pct(lambda r: bool(r.text)):>6s} {pct(lambda r: r.place.geometry is not None):>6s} "
                     f"{pct(lambda r: bool(r.valid_from)):>6s} {', '.join(f'{d}:{c}' for d, c in doms)}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="neet feeds")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "report"):
        p = sub.add_parser(name)
        p.add_argument("--root", default=os.environ.get("NEET_DATA_ROOT", "data"))
        p.add_argument("--since")
        p.add_argument("--until")
        p.add_argument("--source", action="append")
        if name == "build":
            p.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    errors: list = []
    recs = build_records(Path(a.root), set(a.source) if a.source else None, a.since, a.until, errors)
    if a.cmd == "build":
        n = write_jsonl(recs, Path(a.out))
        print(f"wrote {n} records to {a.out}")
    print(report(recs))
    if errors:
        print(f"{len(errors)} payload(s) skipped:", file=sys.stderr)
        for e in errors[:20]:
            print("  ", e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

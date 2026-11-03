"""`neet heldout <stage>`: one command per stage of the held-out export; each writes its file and a `.meta.json`
sidecar (settings, seed, input hashes, code commit, counts) under --out-dir.

    neet heldout records --root <Drive data root> --out-dir ~/heldout_build [--before ISO] [--travel-rule p5]
    neet heldout pool    --root <Drive data root> --out-dir ~/heldout_build --window-start ISO [--window-end ISO]
                         --curb ~/neet_v1.csv --dev ~/official_sample.json
    neet heldout sample  --out-dir ~/heldout_build --seed N
    neet heldout claims  --out-dir ~/heldout_build --seed N
    neet heldout heldout --out-dir ~/heldout_build [--sheet filled_claim_sheet.csv] [--check heldout_check.py]
    neet heldout docs    --out-dir ~/heldout_build --curb ~/neet_v1.csv

The real draw is Oliver's to trigger once the window start is set; test with a mock window and `--before`.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import docs
from .draw import HELDOUT_COLUMNS, SAMPLE_COLUMNS, SHEET_COLUMNS, claim_sheet, draw_sample, heldout_rows, read_csv
from .pool import POOL_COLUMNS, SOURCES, Settings, build_pool, sha256, write_csv

STAGE_FILES = {"records": "00_records/records.jsonl", "pool": "01_pool/pool.csv", "sample": "02_sample/sample.csv",
               "claims": "03_claims/claim_sheet.csv", "heldout": "03_claims/heldout.csv"}


def git_commit() -> str:
    """HEAD of the NEET checkout, with `-dirty` when tracked files differ from it (the build is then not reproducible
    from the commit alone)."""
    repo = str(Path(__file__).resolve().parents[2])
    try:
        head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", repo, "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return head + ("-dirty" if dirty else "")


def write_meta(path: Path, stage: str, settings: dict, inputs: dict[str, Path], counts: Any) -> None:
    meta = {"stage": stage, "file": path.name, "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "code_commit": git_commit(), "settings": settings,
            "inputs": {k: {"path": str(p), "sha256": sha256(p)} for k, p in inputs.items() if p and Path(p).exists()},
            "output_sha256": sha256(path) if path.exists() else None, "counts": counts}
    Path(str(path) + ".meta.json").write_text(json.dumps(meta, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def guard_out_dir(out: Path) -> Path:
    out = out.expanduser().resolve()
    if "CloudStorage" in out.parts or "Google Drive" in str(out):
        sys.exit(f"refusing to write held-out outputs inside Google Drive ({out}); use a local folder such as ~/heldout_build")
    out.mkdir(parents=True, exist_ok=True)
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="neet heldout", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    p = sub.add_parser("records", help="00: evidence records with item_key / first_seen (neet feeds build)")
    p.add_argument("--root", required=True); p.add_argument("--before", help="ignore captures fetched at or after this ISO UTC instant")
    p.add_argument("--travel-rule", choices=("p5", "title_or_body"), default="p5")
    p = sub.add_parser("pool", help="01: every eligible item in CURB's columns")
    p.add_argument("--root", required=True); p.add_argument("--window-start", required=True); p.add_argument("--window-end")
    p.add_argument("--curb", required=True); p.add_argument("--dev", required=True)
    p.add_argument("--no-durham-crime-all-captured", action="store_true", help="Durham months only when served in the window")
    p.add_argument("--no-street-manager", action="store_true")
    p.add_argument("--durham-roadworks-url", default=Settings.durham_roadworks_url)
    p.add_argument("--max-evidence-chars", type=int, default=Settings.max_evidence_chars,
                   help="drop items whose rendered evidence is longer, never cut (default %(default)s; 0 turns it off)")
    for name in ("sample", "claims"):
        p = sub.add_parser(name, help={"sample": "02: seeded stratified draw", "claims": "03: claim sheet, strategies drawn"}[name])
        p.add_argument("--seed", type=int, required=True)
    p = sub.add_parser("heldout", help="03: heldout.csv in CURB's format from a filled claim sheet")
    p.add_argument("--sheet", help="filled claim sheet (default: 03_claims/claim_sheet.csv)")
    p.add_argument("--check", help="LaSSI's heldout_check.py, run unchanged on the output")
    p = sub.add_parser("docs", help="README.md and DATA_DICTIONARY.md (with the template checks against CURB)")
    p.add_argument("--curb", required=True)
    for p in sub.choices.values():
        p.add_argument("--out-dir", required=True)
    a = ap.parse_args(argv)
    out = guard_out_dir(Path(a.out_dir))

    if a.stage == "records":
        from scraper.feeds.build import build_records, write_jsonl
        errors: list = []
        recs = build_records(Path(a.root), set(SOURCES), errors=errors, before=a.before,
                             lineage={"travel_updates": {"rule": a.travel_rule}})
        path = out / STAGE_FILES["records"]
        n = write_jsonl(recs, path)
        counts = {"records": n, "items": len({r.item_key for r in recs}), "payloads_skipped": len(errors)}
        write_meta(path, "records", {"root": a.root, "before": a.before, "travel_rule": a.travel_rule, "sources": list(SOURCES)}, {}, counts)
        print(json.dumps(counts))
    elif a.stage == "pool":
        settings = Settings(window_start=a.window_start, window_end=a.window_end,
                            durham_crime_all_captured=not a.no_durham_crime_all_captured, street_manager=not a.no_street_manager,
                            durham_roadworks_url=a.durham_roadworks_url, max_evidence_chars=a.max_evidence_chars or None)
        records = out / STAGE_FILES["records"]
        rows, report = build_pool(records, Path(a.root), settings, Path(a.curb).expanduser(), Path(a.dev).expanduser())
        path = out / STAGE_FILES["pool"]
        write_csv(path, rows, POOL_COLUMNS)
        write_meta(path, "pool", report["settings"], {"records": records, "curb": Path(a.curb).expanduser(), "dev": Path(a.dev).expanduser()},
                   {k: v for k, v in report.items() if k != "settings"})
        print(json.dumps(report["counts"], indent=1))
    elif a.stage == "sample":
        pool = out / STAGE_FILES["pool"]
        rows, report = draw_sample(read_csv(pool), a.seed)
        path = out / STAGE_FILES["sample"]
        write_csv(path, rows, SAMPLE_COLUMNS)
        write_meta(path, "sample", {"seed": a.seed}, {"pool": pool}, report)
        print(json.dumps(report, indent=1))
    elif a.stage == "claims":
        sample = out / STAGE_FILES["sample"]
        rows, report = claim_sheet(read_csv(sample), a.seed)
        path = out / STAGE_FILES["claims"]
        write_csv(path, rows, SHEET_COLUMNS)
        write_meta(path, "claims", {"seed": a.seed}, {"sample": sample}, report)
        print(json.dumps(report, indent=1))
    elif a.stage == "heldout":
        sheet = Path(a.sheet).expanduser() if a.sheet else out / STAGE_FILES["claims"]
        sample = out / STAGE_FILES["sample"]
        rows, problems = heldout_rows(read_csv(sheet), read_csv(sample))
        if problems:
            print(f"claim sheet not ready ({len(problems)} problems); heldout.csv not written:", file=sys.stderr)
            for pr in problems[:30]:
                print("  ", pr, file=sys.stderr)
            return 1
        path = out / STAGE_FILES["heldout"]
        write_csv(path, rows, HELDOUT_COLUMNS)
        checked = None
        if a.check:
            res = subprocess.run([sys.executable, "-I", str(Path(a.check).expanduser()), str(path)], capture_output=True, text=True)
            checked = {"checker": a.check, "exit": res.returncode, "output": (res.stdout or res.stderr).strip()[:2000]}
            print(checked["output"])
        write_meta(path, "heldout", {}, {"sheet": sheet, "sample": sample, **({"checker": Path(a.check).expanduser()} if a.check else {})},
                   {"rows": len(rows), "check": checked})
        if checked and checked["exit"] != 0:
            return 1
    elif a.stage == "docs":
        docs.write_docs(out, Path(a.curb).expanduser())
        print(f"wrote {out / 'README.md'} and {out / 'DATA_DICTIONARY.md'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

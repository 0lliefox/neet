"""Stages 2-4: the seeded stratified sample, the claim sheet with pre-drawn strategies, and heldout.csv.

Everything random goes through one `random.Random(seed)` per stage, over inputs sorted by `item_key`, so a stage
rebuilt from the same input and seed gives the same output. NEET writes no claims and no labels for them: the
claim sheet's claim cells are empty, and heldout.csv is built only from a claim sheet Oliver has filled."""
from __future__ import annotations

import csv
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from .pool import CURB_COLUMNS, POOL_COLUMNS, PROVENANCE

# 20 Oct brief, section 3 (decided 11 Oct)
TARGETS = {("roadworks", "newcastle"): 18, ("transport", "newcastle"): 18, ("crime", "newcastle"): 17,
           ("weather", "newcastle"): 17, ("crime", "durham"): 10, ("weather", "durham"): 10, ("roadworks", "durham"): 10}

# CURB's strategy mix per domain (neet_v1.csv, counts per 7 or 8 items; 3 Nov export brief, section 5)
MIX = {
    "crime": {"S": {"approximate_paraphrase": 7},
              "R": {"outcome_mismatch": 3, "location_mismatch": 2, "negation": 2},
              "N": {"unsupported_extra_detail": 7}},
    "roadworks": {"S": {"direct_support": 7},
                  "R": {"location_mismatch": 4, "time_mismatch": 2, "outcome_mismatch": 1},
                  "N": {"unsupported_extra_detail": 6, "cause_mismatch": 1}},
    "transport": {"S": {"direct_support": 5, "approximate_paraphrase": 3},
                  "R": {"negation": 5, "location_mismatch": 3},
                  "N": {"cause_mismatch": 5, "unsupported_extra_detail": 2, "time_mismatch": 1}},
    "weather": {"S": {"approximate_paraphrase": 7, "direct_support": 1},
                "R": {"negation": 7, "location_mismatch": 1},
                "N": {"time_mismatch": 7, "unsupported_extra_detail": 1}},
}
LABELS = {"S": "Supported", "R": "Refuted", "N": "Not Enough Evidence"}
SAMPLE_COLUMNS = ["item_id"] + POOL_COLUMNS[1:]
SHEET_COLUMNS = ["item_id", "domain", "city", "evidence_text", "date_time", "location",
                 "S_strategy", "S_label", "S_claim", "R_strategy", "R_label", "R_claim", "N_strategy", "N_label", "N_claim"]
HELDOUT_COLUMNS = CURB_COLUMNS + PROVENANCE


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def draw_sample(pool: list[dict], seed: int, targets: Optional[dict] = None) -> tuple[list[dict], dict]:
    """Seeded stratified draw (domain x city). Weather draws model runs first and then one forecast step of each run,
    so no two items are near-identical hours of one run. A stratum smaller than its target is taken whole."""
    targets = targets or TARGETS
    rng = random.Random(seed)
    by = defaultdict(list)
    for r in pool:
        by[(r["domain"], r["city"])].append(r)
    out, report = [], {}
    for (domain, city), target in sorted(targets.items()):
        rows = sorted(by.get((domain, city), []), key=lambda r: r["item_key"])
        if domain == "weather":
            runs = defaultdict(list)
            for r in rows:
                runs[r["model_run"]].append(r)
            chosen_runs = rng.sample(sorted(runs), min(target, len(runs)))
            picked = [rng.choice(runs[run]) for run in chosen_runs]
            available = len(runs)
        else:
            picked = rng.sample(rows, min(target, len(rows)))
            available = len(rows)
        for n, r in enumerate(picked, 1):
            out.append({**r, "item_id": f"test_{city}_{domain}_{n:03d}"})
        report[f"{domain}/{city}"] = {"target": target, "available": available, "drawn": len(picked)}
    return out, report


def allocate(mix: dict[str, int], n: int) -> list[str]:
    """`n` strategies in CURB's proportions (largest remainder; ties broken by name, so it is deterministic)."""
    total = sum(mix.values())
    exact = {k: n * v / total for k, v in mix.items()}
    alloc = {k: int(x) for k, x in exact.items()}
    for k in sorted(exact, key=lambda k: (-(exact[k] - alloc[k]), k))[: n - sum(alloc.values())]:
        alloc[k] += 1
    return [k for k in sorted(alloc) for _ in range(alloc[k])]


def claim_sheet(sample: list[dict], seed: int) -> tuple[list[dict], dict]:
    """One row per sampled item; S, R and N strategies drawn per domain (both cities together) in CURB's mix. The
    label is fixed by the slot (time_mismatch is Refuted as R, Not Enough Evidence as N)."""
    rng = random.Random(seed)
    rows = sorted(sample, key=lambda r: r["item_id"])
    by_domain = defaultdict(list)
    for r in rows:
        by_domain[r["domain"]].append(r)
    drawn: dict[str, dict[str, str]] = defaultdict(dict)
    report: dict = {}
    for domain in sorted(by_domain):
        items = by_domain[domain]
        report[domain] = {}
        for slot in "SRN":
            strategies = allocate(MIX[domain][slot], len(items))
            rng.shuffle(strategies)
            for r, st in zip(items, strategies):
                drawn[r["item_id"]][slot] = st
            report[domain][slot] = dict(sorted(Counter(strategies).items()))
    out = []
    for r in rows:
        d = drawn[r["item_id"]]
        out.append({"item_id": r["item_id"], "domain": r["domain"], "city": r["city"], "evidence_text": r["evidence_text"],
                    "date_time": r["date_time"], "location": r["location"],
                    **{f"{slot}_{k}": v for slot in "SRN" for k, v in (("strategy", d[slot]), ("label", LABELS[slot]), ("claim", ""))}})
    return out, report


def heldout_rows(sheet: list[dict], sample: list[dict]) -> tuple[list[dict], list[str]]:
    """CURB-format rows (S, R, N per item) from a filled claim sheet; problems are returned, not raised."""
    by_id = {r["item_id"]: r for r in sample}
    out, problems = [], []
    for s in sorted(sheet, key=lambda r: r["item_id"]):
        src = by_id.get(s["item_id"])
        if src is None:
            problems.append(f"{s['item_id']}: not in the sample"); continue
        if s["evidence_text"] != src["evidence_text"]:
            problems.append(f"{s['item_id']}: evidence_text differs from the sample")
        for slot in "SRN":
            claim = (s.get(f"{slot}_claim") or "").strip()
            if not claim:
                problems.append(f"{s['item_id']}_{slot}: no claim yet")
            if s.get(f"{slot}_label") != LABELS[slot]:
                problems.append(f"{s['item_id']}_{slot}: label changed from {LABELS[slot]}")
            out.append({**{c: src.get(c, "") for c in HELDOUT_COLUMNS}, "item_id": f"{s['item_id']}_{slot}",
                        "claim_text": claim, "label": s.get(f"{slot}_label") or LABELS[slot], "claim_type": s.get(f"{slot}_strategy") or ""})
    return out, problems

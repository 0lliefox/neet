"""Check a CURB-format CSV before LaSSI builds case files from it (held-out set, Nov 2026). Standard library only, so
NEET's export test can run it unchanged. Exit 0 and print a count if the CSV is accepted; exit 1 with every problem.
Checks: CURB's columns; item_id ends in _S, _R or _N; one S, R and N row per item with identical evidence_text;
evidence_text, claim_text, date_time and source_name non-empty; label is one of the three CURB labels.
usage: heldout_check.py <csv>"""
import collections, csv, sys

COLS = ["item_id", "domain", "evidence_text", "source_name", "source_page", "source_url", "date_time", "location",
        "claim_text", "label", "claim_type"]
LABELS = {"Supported", "Refuted", "Not Enough Evidence"}


def check(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    if not rows:
        return rows, ["no rows"]
    errors = [f"missing column {c}" for c in COLS if c not in rows[0]]
    if errors:
        return rows, errors
    items = collections.defaultdict(dict)
    for r in rows:
        iid = r["item_id"]
        if iid[-2:] not in ("_S", "_R", "_N"):
            errors.append(f"{iid}: item_id must end in _S, _R or _N"); continue
        for c in ("evidence_text", "claim_text", "date_time", "source_name"):
            if not r[c].strip():
                errors.append(f"{iid}: empty {c}")
        if r["label"] not in LABELS:
            errors.append(f"{iid}: label {r['label']!r}")
        if iid[-1] in items[iid[:-2]]:
            errors.append(f"{iid}: duplicate row")
        items[iid[:-2]][iid[-1]] = r
    for case, slots in items.items():
        if sorted(slots) != ["N", "R", "S"]:
            errors.append(f"{case}: has {''.join(sorted(slots))}, needs S, R and N")
        elif len({s["evidence_text"] for s in slots.values()}) != 1:
            errors.append(f"{case}: the three rows carry different evidence")
    return rows, errors


if __name__ == "__main__":
    rows, errors = check(sys.argv[1])
    if errors:
        sys.exit("CSV rejected:\n  " + "\n  ".join(errors))
    print(f"CSV accepted: {len({r['item_id'][:-2] for r in rows})} items, {len(rows)} pairs")

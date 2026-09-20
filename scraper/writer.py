from __future__ import annotations

import csv
import dataclasses
import logging
from pathlib import Path

from scraper.base import EvidenceItem

logger = logging.getLogger(__name__)

CSV_FIELDS = [
    "item_id", "domain", "evidence_text", "source_name", "source_page",
    "source_url", "date_time", "location", "claim_text", "label", "claim_type",
]

# Default claim labels — one row per label written for each evidence snippet.
# The claim_text and claim_type columns are left blank for manual entry.
DEFAULT_CLAIM_LABELS = ["Supported", "Refuted", "Not Enough Evidence"]

# Suffix appended to the base item_id for each claim row
LABEL_SUFFIXES = {
    "Supported": "S",
    "Refuted": "R",
    "Not Enough Evidence": "N",
}


def write_csv(
    items: list[EvidenceItem],
    output_path: str | Path,
    claim_labels: list[str] | None = None,
) -> None:
    """Write the dataset CSV.

    For each evidence item, writes one row per claim label (default: Supported,
    Refuted, Not Enough Evidence) with claim_text and claim_type left blank for
    manual entry.  This produces 3 × len(items) rows in total.

    Row ID format:  {base_item_id}_{suffix}
      e.g.  roadworks_001_S  (Supported)
            roadworks_001_R  (Refuted)
            roadworks_001_N  (Not Enough Evidence)
    """
    if claim_labels is None:
        claim_labels = DEFAULT_CLAIM_LABELS

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()

        for item in items:
            base = dataclasses.asdict(item)
            for label in claim_labels:
                suffix = LABEL_SUFFIXES.get(label, label[:1].upper())
                row = {
                    **base,
                    "item_id": f"{item.item_id}_{suffix}",
                    "label": label,
                    "claim_text": "",   # filled in manually
                    "claim_type": "",   # filled in manually
                }
                writer.writerow(row)
                total_rows += 1

    evidence_count = len(items)
    logger.info("Wrote %d rows (%d evidence × %d claims) to %s",
                total_rows, evidence_count, len(claim_labels), output_path)
    print(f"Saved {evidence_count} evidence items × {len(claim_labels)} claims "
          f"= {total_rows} rows → {output_path}")

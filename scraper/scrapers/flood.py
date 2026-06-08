"""Environment Agency flood warnings scraper.

Source: https://environment.data.gov.uk/flood-monitoring/
Method: open JSON REST API — no key required.

Covers Northumberland and Tyne & Wear. If no active warnings exist,
returns a single "no warnings" snippet which is useful as a refuted-claim
evidence item (e.g. "There is an active flood warning in Northumberland" → Refuted).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from scraper.base import BaseScraper, EvidenceItem

logger = logging.getLogger(__name__)

API_BASE = "https://environment.data.gov.uk/flood-monitoring"
HEADERS = {"User-Agent": "Mozilla/5.0 (research scraper; neet dataset project)"}

SEVERITY_LABELS = {
    1: "Severe Flood Warning",
    2: "Flood Warning",
    3: "Flood Alert",
    4: "Warning No Longer in Force",
}

# Counties / areas to search
SEARCH_AREAS = ["Northumberland", "Tyne and Wear"]

class FloodScraper(BaseScraper):
    domain = "weather"
    source_name = "Environment Agency Flood Monitoring API"
    source_url = f"{API_BASE}/id/floods"

    def __init__(self, max_items: int = 10, areas: list[str] | None = None) -> None:
        super().__init__(max_items)
        self._areas = areas or SEARCH_AREAS

    def scrape(self) -> list[EvidenceItem]:
        scraped_at = datetime.now(timezone.utc).isoformat()
        items: list[EvidenceItem] = []

        for area in self._areas:
            if len(items) >= self.max_items:
                break
            items.extend(self._fetch_area(area, scraped_at))

        if not items:
            # No active warnings — return a single informational snippet
            items.append(EvidenceItem(
                item_id=self._next_id(),
                domain=self.domain,
                evidence_text=(
                    "The Environment Agency reports no active flood warnings "
                    "or alerts for Northumberland or Tyne and Wear."
                ),
                source_name=self.source_name,
                source_page="Flood Warnings List",
                source_url=self.source_url,
                date_time=scraped_at,
                location="Northumberland / Tyne and Wear",
            ))
            logger.info("Flood API: no active warnings; returning synthetic item")
        else:
            logger.info("Flood API: collected %d items", len(items))

        return items[: self.max_items]

    def _fetch_area(self, county: str, scraped_at: str) -> list[EvidenceItem]:
        try:
            resp = requests.get(
                f"{API_BASE}/id/floods",
                params={"county": county, "_limit": 20},
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.error("Flood API fetch failed for %s: %s", county, exc)
            return []

        results: list[EvidenceItem] = []
        for entry in data.get("items", []):
            severity = SEVERITY_LABELS.get(entry.get("severityLevel", 0), "Flood Warning")
            description = entry.get("description", "")
            message = entry.get("message", "")
            area_name = entry.get("floodAreaID", county).replace("-", " ").title()
            time_raised = entry.get("timeRaised", scraped_at)

            evidence = (
                f"{severity} issued for {area_name}."
                + (f" {message}" if message else "")
            ).strip()
            if not evidence:
                evidence = description

            results.append(EvidenceItem(
                item_id=self._next_id(),
                domain=self.domain,
                evidence_text=evidence,
                source_name=self.source_name,
                source_page="Flood Warnings List",
                source_url=self.source_url,
                date_time=time_raised,
                location=county,
            ))

        return results

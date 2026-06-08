"""Northumbria Police / Police.uk crime report scraper.

Source:  Police.uk open data API — no key required.
  https://data.police.uk/api/crimes-street/all-crime
  Returns street-level crime incidents within ~1 mile of Newcastle city centre.

Each evidence item reports a recorded crime incident (category, street, outcome).

Domain: crime
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from scraper.base import BaseScraper, EvidenceItem

logger = logging.getLogger(__name__)

API_BASE = "https://data.police.uk/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (research scraper; neet dataset project)"}

# Newcastle city centre coordinates
DEFAULT_LAT = 54.9781
DEFAULT_LON = -1.6162

# Human-readable category labels (Police.uk slugs → display names)
_CATEGORY_LABELS: dict[str, str] = {
    "all-crime": "Crime",
    "anti-social-behaviour": "Anti-social behaviour",
    "bicycle-theft": "Bicycle theft",
    "burglary": "Burglary",
    "criminal-damage-arson": "Criminal damage and arson",
    "drugs": "Drugs offence",
    "other-theft": "Theft",
    "possession-of-weapons": "Possession of weapons",
    "public-order": "Public order offence",
    "robbery": "Robbery",
    "shoplifting": "Shoplifting",
    "theft-from-the-person": "Theft from the person",
    "vehicle-crime": "Vehicle crime",
    "violent-crime": "Violent crime",
    "other-crime": "Other crime",
    "violence-and-sexual-offences": "Violence and sexual offences",
}

class PoliceScraper(BaseScraper):
    domain = "crime"
    source_name = "Northumbria Police / Police.uk"
    source_url = f"{API_BASE}/crimes-street/all-crime"

    def __init__(
        self,
        max_items: int = 10,
        lat: float = DEFAULT_LAT,
        lon: float = DEFAULT_LON,
        date: str | None = None,
    ) -> None:
        super().__init__(max_items)
        self._lat = lat
        self._lon = lon
        # date format: YYYY-MM (defaults to the most recent available month)
        self._date = date

    def scrape(self) -> list[EvidenceItem]:
        scraped_at = datetime.now(timezone.utc).isoformat()

        items = self._scrape_api(scraped_at)

        if not items:
            logger.info("Police: no items collected from API")
        else:
            logger.info("Police: %d items collected", len(items))

        return items[: self.max_items]

    # Police.uk API

    def _fetch_records(self) -> list | None:
        """Fetch crime records, falling back to earlier months if the latest has sparse data."""
        if self._date:
            return self._fetch_month(self._date)

        # Try the last 6 months in reverse order; use the first month with ≥ 50 records
        now = datetime.now(timezone.utc)
        for months_back in range(2, 8):
            year = now.year
            month = now.month - months_back
            while month <= 0:
                month += 12
                year -= 1
            date_str = f"{year}-{month:02d}"
            records = self._fetch_month(date_str)
            if records is not None and len(records) >= 50:
                logger.info("Police API: using %s (%d records)", date_str, len(records))
                return records

        logger.warning("Police API: no month with sufficient data found")
        return None

    def _fetch_month(self, date: str) -> list | None:
        try:
            resp = requests.get(
                f"{API_BASE}/crimes-street/all-crime",
                params={"lat": self._lat, "lng": self._lon, "date": date},
                headers=HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.error("Police API fetch failed (%s): %s", date, exc)
            return None
        except ValueError as exc:
            logger.error("Police API JSON parse error (%s): %s", date, exc)
            return None

        if not isinstance(data, list):
            logger.error("Police API returned unexpected structure for %s", date)
            return None
        return data

    def _scrape_api(self, scraped_at: str) -> list[EvidenceItem]:
        records = self._fetch_records()
        if records is None:
            return []

        # Group records by category, then round-robin one per category for variety
        from collections import defaultdict
        by_category: dict[str, list] = defaultdict(list)
        for record in records:
            by_category[record.get("category", "other-crime")].append(record)

        # Interleave: pick the first record from each category in turn
        interleaved: list = []
        category_lists = list(by_category.values())
        max_len = max(len(v) for v in category_lists)
        for i in range(max_len):
            for cat_records in category_lists:
                if i < len(cat_records):
                    interleaved.append(cat_records[i])

        seen_streets: set[str] = set()
        items: list[EvidenceItem] = []

        for record in interleaved:
            if len(items) >= self.max_items:
                break

            category_slug = record.get("category", "other-crime")
            category = _CATEGORY_LABELS.get(category_slug, category_slug.replace("-", " ").title())

            loc_info = record.get("location", {})
            street = loc_info.get("street", {}).get("name", "Newcastle")
            street = street.title()

            month = record.get("month", scraped_at[:7])

            outcome_status = record.get("outcome_status") or {}
            outcome = outcome_status.get("category", "") if outcome_status else ""

            # One item per unique street
            if street.lower() in seen_streets:
                continue
            seen_streets.add(street.lower())

            evidence = self._format_api_evidence(category, street, month, outcome)

            items.append(EvidenceItem(
                item_id=self._next_id(),
                domain=self.domain,
                evidence_text=evidence,
                source_name="Police.uk",
                source_page="Street-level crime data",
                source_url=f"{API_BASE}/crimes-street/all-crime",
                date_time=f"{month}-01T00:00:00+00:00",
                location=street,
            ))

        return items

    @staticmethod
    def _format_api_evidence(
        category: str, street: str, month: str, outcome: str
    ) -> str:
        try:
            dt = datetime.strptime(month, "%Y-%m")
            month_label = dt.strftime("%B %Y")
        except ValueError:
            month_label = month

        base = f"{category} recorded on {street}, Newcastle, in {month_label}."
        if outcome:
            base += f" Outcome: {outcome}."
        return base

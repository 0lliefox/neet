"""Nexus Metro live travel news scraper.

Source: https://www.nexus.org.uk/metro/updates
Method: requests + BeautifulSoup (page is static-enough HTML).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from scraper.base import BaseScraper, EvidenceItem

logger = logging.getLogger(__name__)

SOURCE_URL = "https://www.nexus.org.uk/metro/updates"
HEADERS = {"User-Agent": "Mozilla/5.0 (research scraper; neet dataset project)"}

class NexusScraper(BaseScraper):
    domain = "transport"
    source_name = "Nexus Metro Updates"
    source_url = SOURCE_URL

    def scrape(self) -> list[EvidenceItem]:
        try:
            resp = requests.get(SOURCE_URL, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Nexus fetch failed: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        items: list[EvidenceItem] = []
        scraped_at = datetime.now(timezone.utc).isoformat()

        # The page uses <h2> headings followed by <p> siblings.
        skip_title_keywords = ("car park changes", "live train locations",
                               "about metro", "talk to us", "information",
                               "planning a journey", "bikes & e-scooters",
                               "bikes and e-scooters")
        skip_body_keywords = ("to ensure our train service", "type of work taking place",
                              "for the latest service updates", "pop app",
                              "click here", "view the", "e-scooter", "escooter",
                              "bikes are permitted", "bikes and e-scooters")

        # Page uses Drupal views: each update is a div.views-row.
        for row in soup.find_all("div", class_="views-row"):
            if len(items) >= self.max_items:
                break

            heading = row.find("h2")
            title = heading.get_text(strip=True) if heading else ""

            if title and any(kw in title.lower() for kw in skip_title_keywords):
                continue

            # Capture the full text of the row before modifying anything to preserve location data
            full_row_text = row.get_text(" ", strip=True)

            # Extract paragraphs, removing any location text before a <br/> tag
            raw_paras = []
            for child in row.children:
                if getattr(child, "name", None) == "p":
                    inner_html = child.decode_contents()
                    # Split at the first <br>, <br/>, or <br /> tag
                    parts = re.split(r'<br\s*/?>', inner_html, flags=re.IGNORECASE, maxsplit=1)
                    # Take the last part (everything after the <br>, or the whole string if no <br>)
                    clean_text = BeautifulSoup(parts[-1], "lxml").get_text(" ", strip=True)
                    raw_paras.append(clean_text)

            paras = [
                p for p in raw_paras
                if len(p) > 25 and not any(kw in p.lower() for kw in skip_body_keywords)
            ]

            if not paras:
                continue

            if title and title.lower() not in ("ongoing work",):
                first_sentence = re.split(r"(?<=[.!?])\s", paras[0])[0]
                evidence = f"{title}: {first_sentence}"
                evidence_items_for_row = [evidence]
            else:
                evidence_items_for_row = [
                    re.split(r"(?<=[.!?])\s", p)[0] for p in paras
                ]

            for evidence in evidence_items_for_row:
                if len(items) >= self.max_items:
                    break
                if len(evidence) < 20:
                    continue
                evidence = evidence[:250].rsplit(" ", 1)[0] if len(evidence) > 250 else evidence

                # Extract location using the full original text so stripped stations aren't missed
                location = _extract_location(full_row_text)

                items.append(EvidenceItem(
                    item_id=self._next_id(),
                    domain=self.domain,
                    evidence_text=evidence,
                    source_name=self.source_name,
                    source_page="Metro Live Travel News",
                    source_url=SOURCE_URL,
                    date_time=scraped_at,
                    location=location,
                ))

        logger.info("Nexus: collected %d items", len(items))
        return items

_METRO_LOCATIONS = [
    "South Shields", "Pelaw", "Haymarket", "Fawdon", "Benton",
    "South Gosforth", "Whitley Bay", "Monkseaton", "University",
    "Monument", "Central Station", "Gateshead", "Airport",
    "Sunderland", "St James", "Jesmond", "Byker", "Wallsend",
    "North Shields", "Tynemouth", "Cullercoats", "Longbenton",
    "Four Lane Ends", "Regent Centre", "Callerton", "Kenton",
]

def _extract_location(text: str) -> str:
    for loc in _METRO_LOCATIONS:
        if loc.lower() in text.lower():
            return loc
    return "Tyne and Wear Metro"
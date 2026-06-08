"""Tyne Tunnels live status scraper.

Source: https://www.thetynentunnels.co.uk/
Method: requests + BeautifulSoup (main/status page).
Fallback: Traffic England DATEX II feed filtered for Tyne Tunnel references.

The A19 Tyne Tunnel runs in two separate bores — northbound and southbound.
Evidence items report the operational status and any active incidents.

Note: The TT2 website structure may change; adjust CSS selectors in
_parse_status_page() if items stop being collected.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from scraper.base import BaseScraper, EvidenceItem

logger = logging.getLogger(__name__)

STATUS_URL = "https://www.tt2.co.uk/"
DATEX_URL = "https://hatrafficinfo.dft.gov.uk/feeds/datex2/England/CurrentRoadworks/content.xml"
HEADERS = {"User-Agent": "Mozilla/5.0 (research scraper; neet dataset project)"}

# Text indicating a bore is fully operational (case-insensitive)
_OPEN_WORDS = re.compile(r"\b(open|normal|no restrictions?|all lanes? open)\b", re.IGNORECASE)
# Text indicating some restriction
_RESTRICTION_WORDS = re.compile(
    r"\b(closed|closure|lane restriction|reduced speed|incident|delays?|queuing)\b",
    re.IGNORECASE,
)
_DIRECTIONS = re.compile(r"\b(northbound|southbound)\b", re.IGNORECASE)

class TyneTunnelsScraper(BaseScraper):
    domain = "transport"
    source_name = "Tyne Tunnels (TT2)"
    source_url = STATUS_URL

    def scrape(self) -> list[EvidenceItem]:
        scraped_at = datetime.now(timezone.utc).isoformat()

        items = self._scrape_website(scraped_at)
        if items:
            return items[: self.max_items]

        logger.warning("Tyne Tunnels: website scrape returned nothing; trying DATEX II fallback")
        items = self._scrape_datex(scraped_at)
        if items:
            return items[: self.max_items]

        # No disruptions found — emit a single "all clear" item (mirrors flood scraper pattern)
        logger.info("Tyne Tunnels: no disruptions detected; returning all-clear item")
        return [EvidenceItem(
            item_id=self._next_id(),
            domain=self.domain,
            evidence_text=(
                "The Tyne Tunnels (A19) are operating normally with no reported "
                "closures or restrictions on either bore."
            ),
            source_name=self.source_name,
            source_page="Tyne Tunnels Live Status",
            source_url=STATUS_URL,
            date_time=scraped_at,
            location="Tyne Tunnel / A19",
        )]

    # Primary: TT2 website

    def _scrape_website(self, scraped_at: str) -> list[EvidenceItem]:
        try:
            resp = requests.get(STATUS_URL, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Tyne Tunnels website fetch failed: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        items: list[EvidenceItem] = []

        # Strategy 1: look for explicit status blocks keyed on direction headings
        items.extend(self._parse_direction_blocks(soup, scraped_at))
        if items:
            logger.info("Tyne Tunnels: %d items from direction blocks", len(items))
            return items

        # Strategy 2: scan all text for sentences that mention tunnel + restriction
        items.extend(self._parse_free_text(soup, scraped_at))
        if items:
            logger.info("Tyne Tunnels: %d items from free-text scan", len(items))
        return items

    def _parse_direction_blocks(self, soup: BeautifulSoup, scraped_at: str) -> list[EvidenceItem]:
        """Look for heading + sibling status pattern (northbound / southbound blocks)."""
        items: list[EvidenceItem] = []
        for tag in soup.find_all(string=_DIRECTIONS):
            parent = tag.parent
            if parent is None:
                continue
            direction = _DIRECTIONS.search(tag).group(0).title()
            # Look for status text in the same container or the next sibling
            container = parent.parent or parent
            status_text = container.get_text(" ", strip=True)

            if _RESTRICTION_WORDS.search(status_text):
                sentence = _first_sentence(status_text)
                items.append(EvidenceItem(
                    item_id=self._next_id(),
                    domain=self.domain,
                    evidence_text=f"Tyne Tunnel {direction}: {sentence}",
                    source_name=self.source_name,
                    source_page="Tyne Tunnels Live Status",
                    source_url=STATUS_URL,
                    date_time=scraped_at,
                    location="Tyne Tunnel / A19",
                ))
            elif _OPEN_WORDS.search(status_text):
                items.append(EvidenceItem(
                    item_id=self._next_id(),
                    domain=self.domain,
                    evidence_text=f"The Tyne Tunnel {direction} bore is open with no restrictions.",
                    source_name=self.source_name,
                    source_page="Tyne Tunnels Live Status",
                    source_url=STATUS_URL,
                    date_time=scraped_at,
                    location="Tyne Tunnel / A19",
                ))
            if len(items) >= self.max_items:
                break
        return items

    def _parse_free_text(self, soup: BeautifulSoup, scraped_at: str) -> list[EvidenceItem]:
        """Scan paragraphs for tunnel-related restriction mentions."""
        items: list[EvidenceItem] = []
        for tag in soup.find_all(["p", "li", "div"]):
            text = tag.get_text(" ", strip=True)
            if len(text) < 20 or len(text) > 500:
                continue
            if not re.search(r"\btunnel\b", text, re.IGNORECASE):
                continue
            if not _RESTRICTION_WORDS.search(text):
                continue
            sentence = _first_sentence(text)
            items.append(EvidenceItem(
                item_id=self._next_id(),
                domain=self.domain,
                evidence_text=sentence[:300],
                source_name=self.source_name,
                source_page="Tyne Tunnels Live Status",
                source_url=STATUS_URL,
                date_time=scraped_at,
                location="Tyne Tunnel / A19",
            ))
            if len(items) >= self.max_items:
                break
        return items

    # Fallback: DATEX II feed filtered for Tyne Tunnel

    def _scrape_datex(self, scraped_at: str) -> list[EvidenceItem]:
        try:
            import lxml.etree as etree
            resp = requests.get(DATEX_URL, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            root = etree.fromstring(resp.content)
        except Exception as exc:
            logger.error("Tyne Tunnels DATEX II fallback failed: %s", exc)
            return []

        ns = {"d2": "http://datex2.eu/schema/2/2_0"}
        situations = root.findall(".//d2:situation", ns) or root.findall(".//situation")
        tunnel_re = re.compile(r"\btyne tunnel\b", re.IGNORECASE)

        items: list[EvidenceItem] = []
        for situation in situations:
            if len(items) >= self.max_items:
                break
            loc = _datex_text(situation, [
                ".//d2:locationDescription", ".//locationDescription",
                ".//d2:roadName", ".//roadName",
            ], ns)
            comment = _datex_text(situation, [
                ".//d2:comment", ".//comment",
                ".//d2:generalPublicComment//d2:comment",
            ], ns)
            combined = f"{loc} {comment}".strip()
            if not tunnel_re.search(combined):
                continue
            items.append(EvidenceItem(
                item_id=self._next_id(),
                domain=self.domain,
                evidence_text=_first_sentence(combined)[:300],
                source_name=self.source_name,
                source_page="National Highways DATEX II Feed",
                source_url=DATEX_URL,
                date_time=scraped_at,
                location="Tyne Tunnel / A19",
            ))

        logger.info("Tyne Tunnels DATEX II: %d items", len(items))
        return items

def _first_sentence(text: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s", text.strip())[0]
    return sentence[:300].rsplit(" ", 1)[0] if len(sentence) > 300 else sentence

def _datex_text(element, xpaths: list[str], ns: dict) -> str:
    for xpath in xpaths:
        found = element.find(xpath, ns)
        if found is not None and found.text:
            return found.text.strip()
    return ""

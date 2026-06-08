"""Go North East / Arriva North East bus service disruption scraper.

Primary source:   Go North East service updates
  https://www.gonortheast.co.uk/service-updates/
Secondary source: Arriva North East service updates
  https://www.arrivabus.co.uk/north-east/service-updates

Both pages are scraped with requests + BeautifulSoup. If the content is
JS-rendered and BeautifulSoup finds nothing, Playwright is used as a fallback.

Evidence items report disruptions to specific bus routes in Newcastle / Tyne and Wear,
e.g. "Service 21 (Newcastle – Consett): diversion in place via Ponteland Road due to
roadworks on Grandstand Road until 25 April."
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from scraper.base import BaseScraper, EvidenceItem

logger = logging.getLogger(__name__)

GNE_URL = "https://www.gonortheast.co.uk/service-updates/"
ARRIVA_URL = "https://www.arrivabus.co.uk/north-east/service-updates"
HEADERS = {"User-Agent": "Mozilla/5.0 (research scraper; neet dataset project)"}

# Minimum length for a useful disruption snippet
_MIN_LEN = 30

# Place names / route keywords that anchor a snippet to Newcastle / T&W
_NE_FILTER = re.compile(
    r"\b(Newcastle|Gateshead|Sunderland|Tyne|Wear|Northumberland|"
    r"Gosforth|Jesmond|Byker|Heaton|Wallsend|North Shields|South Shields|"
    r"Jarrow|Washington|Cramlington|Whitley Bay|Tynemouth|Blyth|Morpeth|"
    r"Ponteland|Killingworth|Fenham|Elswick|Benwell|Scotswood|"
    r"Blaydon|Dunston|Felling|Low Fell|Gateshead Quays|Stanley|"
    r"Quayside|MetroCentre|Metro Centre|Nexus|Barnes Park|Ridsdale|"
    r"Kimblesworth|Borough Road|Chester-le-Street|Consett|Hexham)\b",
    re.IGNORECASE,
)

class BusScraper(BaseScraper):
    domain = "transport"
    source_name = "Go North East / Arriva North East"
    source_url = GNE_URL

    def scrape(self) -> list[EvidenceItem]:
        scraped_at = datetime.now(timezone.utc).isoformat()

        # Try Go North East first
        items = self._scrape_static(GNE_URL, "Go North East", scraped_at)
        if not items:
            logger.warning("Bus: GNE static scrape empty; trying Playwright")
            items = self._scrape_playwright(GNE_URL, "Go North East", scraped_at)

        # Top up from Arriva if needed
        if len(items) < self.max_items:
            arriva = self._scrape_static(ARRIVA_URL, "Arriva North East", scraped_at)
            if not arriva:
                arriva = self._scrape_playwright(ARRIVA_URL, "Arriva North East", scraped_at)
            items.extend(arriva)

        if not items:
            logger.info("Bus: no disruptions found on either source")
        else:
            logger.info("Bus: %d items collected", len(items))

        return items[: self.max_items]

    # Static scrape

    def _scrape_static(
        self, url: str, operator: str, scraped_at: str
    ) -> list[EvidenceItem]:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Bus static fetch failed (%s): %s", operator, exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        return self._parse_soup(soup, operator, url, scraped_at)

    # Playwright fallback

    def _scrape_playwright(
        self, url: str, operator: str, scraped_at: str
    ) -> list[EvidenceItem]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright not installed; cannot scrape %s", operator)
            return []

        html = ""
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(3000)
                html = page.content()
            finally:
                browser.close()

        if not html:
            return []
        soup = BeautifulSoup(html, "lxml")
        return self._parse_soup(soup, operator, url, scraped_at)

    # Shared parser

    def _parse_soup(
        self, soup: BeautifulSoup, operator: str, url: str, scraped_at: str
    ) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []

        # Strategy 1: h3 headings (GNE uses h3 per disruption) — grab heading + parent text
        for h3 in soup.find_all("h3"):
            if len(items) >= self.max_items:
                break
            parent = h3.find_parent()
            text = parent.get_text(" ", strip=True) if parent else h3.get_text(" ", strip=True)
            item = self._text_to_item(text, operator, url, scraped_at)
            if item:
                items.append(item)

        if items:
            return items

        # Strategy 2: article / card containers with disruption class
        for card in soup.find_all(["article", "li", "div"],
                                  class_=re.compile(r"(update|disruption|alert|service|notice)", re.I)):
            if len(items) >= self.max_items:
                break
            text = card.get_text(" ", strip=True)
            item = self._text_to_item(text, operator, url, scraped_at)
            if item:
                items.append(item)

        if items:
            return items

        # Strategy 3: scan all <p> and <li>
        for tag in soup.find_all(["p", "li"]):
            if len(items) >= self.max_items:
                break
            text = tag.get_text(" ", strip=True)
            item = self._text_to_item(text, operator, url, scraped_at)
            if item:
                items.append(item)

        return items

    def _text_to_item(
        self, text: str, operator: str, url: str, scraped_at: str
    ) -> EvidenceItem | None:
        if len(text) < _MIN_LEN or len(text) > 600:
            return None
        if not _NE_FILTER.search(text):
            return None
        # Must mention a disruption keyword
        if not re.search(
            r"\b(diversion|disruption|cancel|terminat|suspend|delay|replacement|"
            r"not operating|unable to serve|closed|closure|roadworks?|removed|"
            r"out of use|refurbishment|affected|escorted|unable)\b",
            text, re.IGNORECASE,
        ):
            return None

        evidence = _clean_text(text)[:350]
        location = _extract_location(text)

        return EvidenceItem(
            item_id=self._next_id(),
            domain=self.domain,
            evidence_text=evidence,
            source_name=operator,
            source_page="Service Updates",
            source_url=url,
            date_time=scraped_at,
            location=location,
        )

def _clean_text(text: str) -> str:
    """Collapse whitespace and trim to the first 1–2 sentences."""
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:2])

def _extract_location(text: str) -> str:
    _LOCATIONS = [
        "Newcastle", "Gateshead", "Sunderland", "Gosforth", "Jesmond",
        "Byker", "Wallsend", "North Shields", "South Shields", "Jarrow",
        "Washington", "Cramlington", "Whitley Bay", "Tynemouth", "Blyth",
        "Ponteland", "Killingworth", "Fenham", "Blaydon", "Dunston",
        "Felling", "Low Fell",
    ]
    for loc in _LOCATIONS:
        if loc.lower() in text.lower():
            return loc
    return "Tyne and Wear"

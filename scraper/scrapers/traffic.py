"""Traffic England / National Highways alerts scraper.

Source: https://www.trafficengland.com/traffic-alerts
The page is entirely JS-rendered; all content loaded via fetch after page init.

Two-stage approach:
  1. Playwright: headless Chromium, wait for alert cards to render.
  2. DATEX II XML fallback: national DfT feed filtered for NE England roads.
     URL: https://hatrafficinfo.dft.gov.uk/feeds/datex2/England/CurrentRoadworks/content.xml
     This feed had socket issues during initial testing; wrapped in try/except.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from scraper.base import BaseScraper, EvidenceItem

logger = logging.getLogger(__name__)

TRAFFIC_URL = "https://www.trafficengland.com/traffic-alerts"
DATEX_URL = "https://hatrafficinfo.dft.gov.uk/feeds/datex2/England/CurrentRoadworks/content.xml"
HEADERS = {"User-Agent": "Mozilla/5.0 (research scraper; neet dataset project)"}

# Place names that unambiguously locate an alert in Newcastle / Tyne and Wear.
# Used as the FILTER — a place name must appear for an alert to be included.
# Road numbers alone are not sufficient because they span multiple regions
# (e.g. A19 runs from Tyne and Wear all the way through Yorkshire).
NE_PLACE_PATTERNS = re.compile(
    r"\b(Newcastle|Gateshead|Sunderland|"
    r"North Tyneside|South Tyneside|"
    r"Tyne|Wear|Tyne Tunnel|Tyne Bridge|Redheugh|"
    r"Blaydon|Birtley|Morpeth|Hexham|Cramlington|"
    r"Washington|Houghton.le.Spring|Chester-le-Street|"
    r"Gosforth|Jesmond|Byker|Wallsend|Jarrow|"
    r"South Shields|North Shields|Whitley Bay|Tynemouth|"
    r"Ponteland|Killingworth|Longbenton|Benton|"
    r"Westerhope|Denton Burn|Scotswood|Lemington|"
    r"Fenham|Elswick|Benwell|Dunston|Felling|Pelaw|"
    r"Heworth|Wrekenton|Low Fell|Lobley Hill)\b",
    re.IGNORECASE,
)

# Road identifiers within Tyne and Wear — used for location label extraction only,
# not for filtering. Combined with NE_PLACE_PATTERNS in NE_PATTERNS below.
NE_ROAD_PATTERNS = re.compile(
    r"\b(A1M|A1\(M\)|A19|A69|A167|A1058|A184|A194|A693|A696|A697)\b",
    re.IGNORECASE,
)

# Combined pattern for location string extraction (places + roads).
NE_PATTERNS = re.compile(
    r"\b(Newcastle|Gateshead|Sunderland|"
    r"North Tyneside|South Tyneside|"
    r"A1M|A1\(M\)|A19|A69|A167|A1058|A184|A194|A693|A696|A697|"
    r"Tyne Tunnel|Tyne Bridge|Redheugh|"
    r"Blaydon|Birtley|Morpeth|Hexham|Cramlington|"
    r"Washington|Houghton.le.Spring|Chester-le-Street|"
    r"Gosforth|Jesmond|Byker|Wallsend|Jarrow|"
    r"South Shields|North Shields|Whitley Bay|Tynemouth|"
    r"Ponteland|Killingworth|Longbenton|Benton|"
    r"Westerhope|Denton Burn|Scotswood|Lemington|"
    r"Fenham|Elswick|Benwell|Dunston|Felling|Pelaw|"
    r"Heworth|Wrekenton|Low Fell|Lobley Hill)\b",
    re.IGNORECASE,
)

class TrafficScraper(BaseScraper):
    domain = "transport"
    source_name = "Traffic England / National Highways"
    source_url = TRAFFIC_URL

    def __init__(self, max_items: int = 10, ne_patterns: list[str] | None = None) -> None:
        super().__init__(max_items)
        # Full pattern (places + roads) used for location label extraction
        if ne_patterns:
            pattern_str = r"\b(" + "|".join(re.escape(p) for p in ne_patterns) + r")\b"
            self._ne_pattern = re.compile(pattern_str, re.IGNORECASE)
        else:
            self._ne_pattern = NE_PATTERNS
        # Place-names-only pattern used to decide whether to include an alert.
        # Road numbers are excluded from the filter: they span multiple regions
        # and produce false positives (e.g. A19 near Teesside).
        self._ne_place_pattern = NE_PLACE_PATTERNS

    def scrape(self) -> list[EvidenceItem]:
        try:
            items = self._scrape_playwright()
            if items:
                return items
            logger.warning("Traffic England: Playwright returned no items; trying DATEX II fallback")
        except Exception as exc:
            logger.warning("Traffic England: Playwright failed (%s); trying DATEX II fallback", exc)

        try:
            return self._scrape_datex()
        except Exception as exc:
            logger.error("Traffic England: DATEX II fallback also failed: %s", exc)
            return []

    def _scrape_playwright(self) -> list[EvidenceItem]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright not installed")
            return []

        scraped_at = datetime.now(timezone.utc).isoformat()
        items: list[EvidenceItem] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(TRAFFIC_URL, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(3000)

                td_elements = page.query_selector_all(
                    "xpath=//td[contains(text(), 'Location')]"
                )
                logger.info("Traffic England Playwright: found %d detail TDs", len(td_elements))

                for td in td_elements:
                    text = td.inner_text().strip()
                    if not self._ne_place_pattern.search(text):
                        continue

                    evidence = _parse_td_to_evidence(text)
                    if not evidence:
                        continue

                    location = _extract_ne_location(text, self._ne_pattern)
                    items.append(EvidenceItem(
                        item_id=self._next_id(),
                        domain=self.domain,
                        evidence_text=evidence,
                        source_name=self.source_name,
                        source_page="Traffic Alerts",
                        source_url=TRAFFIC_URL,
                        date_time=scraped_at,
                        location=location,
                    ))
                    if len(items) >= self.max_items:
                        break

            finally:
                browser.close()

        logger.info("Traffic England Playwright: %d NE items", len(items))
        return items

    def _scrape_datex(self) -> list[EvidenceItem]:
        import requests
        from lxml import etree

        scraped_at = datetime.now(timezone.utc).isoformat()

        try:
            resp = requests.get(DATEX_URL, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("DATEX II fetch failed: %s", exc)
            return []

        try:
            root = etree.fromstring(resp.content)
        except etree.XMLSyntaxError as exc:
            logger.error("DATEX II XML parse error: %s", exc)
            return []

        ns = {"d2": "http://datex2.eu/schema/2/2_0"}
        situations = root.findall(".//d2:situation", ns) or root.findall(".//situation")

        items: list[EvidenceItem] = []
        for situation in situations:
            if len(items) >= self.max_items:
                break

            loc_desc = _datex_text(situation, [
                ".//d2:locationDescription", ".//locationDescription",
                ".//d2:roadName", ".//roadName",
            ], ns)

            comment = _datex_text(situation, [
                ".//d2:comment", ".//comment",
                ".//d2:generalPublicComment//d2:comment", ".//generalPublicComment//comment",
            ], ns)

            start = _datex_text(situation, [
                ".//d2:overallStartTime", ".//overallStartTime",
                ".//d2:startOfPeriod", ".//startOfPeriod",
            ], ns)

            combined = f"{loc_desc} {comment}".strip()
            if not combined or len(combined) < 15:
                continue

            if not self._ne_place_pattern.search(combined):
                continue

            location = _extract_ne_location(combined, self._ne_pattern)
            evidence = f"National Highways: {combined}"

            items.append(EvidenceItem(
                item_id=self._next_id(),
                domain=self.domain,
                evidence_text=evidence[:400],
                source_name=self.source_name,
                source_page="National Highways DATEX II Feed",
                source_url=DATEX_URL,
                date_time=start or scraped_at,
                location=location,
            ))

        logger.info("Traffic England DATEX II: %d NE items from %d total situations", len(items), len(situations))
        return items

def _datex_text(element, xpaths: list[str], ns: dict) -> str:
    for xpath in xpaths:
        found = element.find(xpath, ns)
        if found is not None and found.text:
            return found.text.strip()
    return ""

def _extract_ne_location(text: str, pattern: re.Pattern = NE_PATTERNS) -> str:
    match = pattern.search(text)
    return match.group(0) if match else "North East England"

def _parse_td_to_evidence(text: str) -> str:
    """Convert a Traffic England detail TD into a single evidence sentence."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip().lower()] = val.strip()

    location = fields.get("location", "")
    reason = fields.get("reason", "")
    status = fields.get("status", "")

    if not location:
        return ""

    parts = [f"Traffic England reports {reason.lower() or 'disruption'} on {location}."]
    if status and status.lower() not in ("currently active",):
        parts.append(status)

    return " ".join(parts)

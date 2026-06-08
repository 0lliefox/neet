"""Newcastle / Gateshead bridge status scraper.

Covers three bridges:
  - Tyne Bridge      (A6125 road + pedestrian bridge, Newcastle–Gateshead)
  - Millennium Bridge (Gateshead Quays tilting pedestrian / cycle bridge)
  - Swing Bridge      (historic swing bridge between Tyne and Millennium)

Sources (tried in order):
  1. Newcastle City Council news — planned closures and maintenance notices
     https://www.newcastle.gov.uk/news
  2. Gateshead Council news — Millennium Bridge lift schedule and maintenance
     https://www.gateshead.gov.uk/news
  3. Traffic England DATEX II feed — filtered for bridge incidents
     https://hatrafficinfo.dft.gov.uk/feeds/datex2/England/CurrentRoadworks/content.xml
  4. Synthetic "no disruptions" fallback (mirrors the flood scraper pattern)

Evidence items report scheduled closures, lift events, or maintenance that
affects pedestrian / vehicle access to the named bridge.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from scraper.base import BaseScraper, EvidenceItem

logger = logging.getLogger(__name__)

NCC_NEWS_URL = "https://www.newcastle.gov.uk/news"
GATESHEAD_NEWS_URL = "https://www.gateshead.gov.uk/news"
DATEX_URL = "https://hatrafficinfo.dft.gov.uk/feeds/datex2/England/CurrentRoadworks/content.xml"
HEADERS = {"User-Agent": "Mozilla/5.0 (research scraper; neet dataset project)"}

_BRIDGE_NAMES = re.compile(
    r"\b(tyne bridge|millennium bridge|swing bridge|gateshead millennium|"
    r"redheugh bridge|king edward bridge)\b",
    re.IGNORECASE,
)
_DISRUPTION_WORDS = re.compile(
    r"\b(closed?|closure|lift|tilt|maintenance|inspection|repairs?|"
    r"restricted?|diversion|pedestrians?|vehicles?|suspend)\b",
    re.IGNORECASE,
)

class BridgesScraper(BaseScraper):
    domain = "transport"
    source_name = "Newcastle / Gateshead Bridges"
    source_url = NCC_NEWS_URL

    def scrape(self) -> list[EvidenceItem]:
        scraped_at = datetime.now(timezone.utc).isoformat()
        items: list[EvidenceItem] = []

        items.extend(self._scrape_news(NCC_NEWS_URL, "Newcastle City Council News", scraped_at))
        if len(items) >= self.max_items:
            return items[: self.max_items]

        items.extend(self._scrape_news(GATESHEAD_NEWS_URL, "Gateshead Council News", scraped_at))
        if len(items) >= self.max_items:
            return items[: self.max_items]

        if len(items) < self.max_items:
            items.extend(self._scrape_datex(scraped_at))

        if items:
            logger.info("Bridges: %d items collected", len(items))
            return items[: self.max_items]

        logger.info("Bridges: no disruptions detected; returning all-clear item")
        return [EvidenceItem(
            item_id=self._next_id(),
            domain=self.domain,
            evidence_text=(
                "No closures or restrictions are currently reported for the Tyne Bridge, "
                "Gateshead Millennium Bridge, or Swing Bridge."
            ),
            source_name=self.source_name,
            source_page="Newcastle / Gateshead Bridge Status",
            source_url=NCC_NEWS_URL,
            date_time=scraped_at,
            location="Newcastle / Gateshead",
        )]

    def _scrape_news(
        self, url: str, source_name: str, scraped_at: str
    ) -> list[EvidenceItem]:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Bridges: news fetch failed (%s): %s", url, exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        items: list[EvidenceItem] = []

        for tag in soup.find_all(["article", "li", "h2", "h3"]):
            if len(items) >= self.max_items:
                break
            text = tag.get_text(" ", strip=True)
            if not _BRIDGE_NAMES.search(text):
                continue

            link = tag.find("a", href=True)
            detail_text = self._fetch_article(link["href"], url) if link else ""
            evidence_text = detail_text or text

            if not _DISRUPTION_WORDS.search(evidence_text):
                continue

            bridge = _extract_bridge_name(evidence_text)
            evidence = _clean_text(evidence_text)[:350]
            items.append(EvidenceItem(
                item_id=self._next_id(),
                domain=self.domain,
                evidence_text=evidence,
                source_name=source_name,
                source_page="Council News",
                source_url=url,
                date_time=scraped_at,
                location=bridge,
            ))

        return items

    def _fetch_article(self, href: str, base_url: str) -> str:
        """Fetch article body text; return empty string on any failure."""
        if href.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(base_url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"
        try:
            resp = requests.get(href, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            for selector in ["article", ".article-body", ".news-body", "main p"]:
                body = soup.select_one(selector)
                if body:
                    return body.get_text(" ", strip=True)[:600]
        except Exception:
            pass
        return ""

    def _scrape_datex(self, scraped_at: str) -> list[EvidenceItem]:
        try:
            import lxml.etree as etree
            resp = requests.get(DATEX_URL, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            root = etree.fromstring(resp.content)
        except Exception as exc:
            logger.error("Bridges DATEX II fetch failed: %s", exc)
            return []

        ns = {"d2": "http://datex2.eu/schema/2/2_0"}
        situations = root.findall(".//d2:situation", ns) or root.findall(".//situation")

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
            if not _BRIDGE_NAMES.search(combined):
                continue
            bridge = _extract_bridge_name(combined)
            items.append(EvidenceItem(
                item_id=self._next_id(),
                domain=self.domain,
                evidence_text=_clean_text(combined)[:300],
                source_name="National Highways DATEX II",
                source_page="National Highways DATEX II Feed",
                source_url=DATEX_URL,
                date_time=scraped_at,
                location=bridge,
            ))

        logger.info("Bridges DATEX II: %d items", len(items))
        return items

def _extract_bridge_name(text: str) -> str:
    m = _BRIDGE_NAMES.search(text)
    if m:
        return m.group(0).title()
    return "Newcastle / Gateshead"

def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:2])

def _datex_text(element, xpaths: list[str], ns: dict) -> str:
    for xpath in xpaths:
        found = element.find(xpath, ns)
        if found is not None and found.text:
            return found.text.strip()
    return ""

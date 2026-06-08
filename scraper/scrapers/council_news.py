"""Newcastle City Council news scraper.

Source: https://www.newcastle.gov.uk/news
Method: requests + BeautifulSoup (static HTML CMS).

Used as:
  - A standalone supplementary source (any domain)
  - A fallback for roadworks.py when the interactive map can't be scraped

Pass `keywords` to filter articles to a particular domain.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from scraper.base import BaseScraper, EvidenceItem

logger = logging.getLogger(__name__)

SOURCE_URL = "https://www.newcastle.gov.uk/news"
HEADERS = {"User-Agent": "Mozilla/5.0 (research scraper; neet dataset project)"}

ROADWORKS_KEYWORDS = [
    "roadworks", "closure", "bridge", "resurfacing", "traffic",
    "road", "works", "diversion", "lane", "tyne bridge",
    "street", "pavement", "construction", "maintenance", "repair",
    "highway", "footpath", "path", "building work",
]

ALL_KEYWORDS: list[str] = []  # empty = no filtering

class CouncilNewsScraper(BaseScraper):
    domain = "roadworks"
    source_name = "Newcastle City Council News"
    source_url = SOURCE_URL

    def __init__(
        self,
        max_items: int = 10,
        keywords: list[str] | None = None,
        domain: str = "roadworks",
    ) -> None:
        super().__init__(max_items)
        self.keywords = keywords if keywords is not None else ROADWORKS_KEYWORDS
        self.domain = domain

    def scrape(self) -> list[EvidenceItem]:
        try:
            resp = requests.get(SOURCE_URL, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Council news fetch failed: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        scraped_at = datetime.now(timezone.utc).isoformat()
        items: list[EvidenceItem] = []

        # Newcastle City Council news page uses <h3> elements with <a> links as titles.
        # Each is followed by optional <p> summary and <time> date in nearby siblings.
        for heading in soup.find_all("h3"):
            if len(items) >= self.max_items:
                break

            link = heading.find("a")
            if not link:
                continue

            title = heading.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            article_url = link.get("href", SOURCE_URL)
            if article_url.startswith("/"):
                article_url = "https://www.newcastle.gov.uk" + article_url

            # Look for date and summary in parent/sibling elements
            parent = heading.parent
            date_tag = parent.find("time") if parent else None
            pub_date = date_tag.get("datetime") or date_tag.get_text(strip=True) if date_tag else scraped_at

            summary_tag = parent.find("p") if parent else None
            summary = summary_tag.get_text(strip=True) if summary_tag else ""

            evidence = title + ("." if not title.endswith(".") else "")
            if summary and len(summary) > 20:
                evidence += f" {summary}"

            # Filter by keywords if specified
            if self.keywords:
                lower = evidence.lower()
                if not any(kw in lower for kw in self.keywords):
                    continue

            items.append(EvidenceItem(
                item_id=self._next_id(),
                domain=self.domain,
                evidence_text=evidence,
                source_name=self.source_name,
                source_page="Newcastle City Council News",
                source_url=article_url,
                date_time=pub_date,
                location="Newcastle upon Tyne",
            ))

        logger.info("Council news: collected %d items (keywords=%s)", len(items), self.keywords)
        return items

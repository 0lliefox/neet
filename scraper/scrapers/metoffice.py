"""Met Office Newcastle weather scraper.

Two paths (tried in order):
  1. DataPoint API  — requires METOFFICE_API_KEY in environment / .env
     Register free at: https://datahub.metoffice.gov.uk/
     Newcastle location ID: 352409

  2. Playwright fallback — headless Chromium scrapes the rendered forecast page
     https://weather.metoffice.gov.uk/forecast/gcybg0rne

In either case, returns up to max_items weather evidence snippets.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SOURCE_URL = "https://weather.metoffice.gov.uk/forecast/gcybg0rne"
DATAPOINT_URL = "https://data.hub.api.metoffice.gov.uk/sitespecific/v0/point/hourly"
NEWCASTLE_LAT = 54.9781
NEWCASTLE_LON = -1.6162

# Met Office weather code → description mapping (DataPoint WX codes)
WX_CODES = {
    0: "Clear night", 1: "Sunny day", 2: "Partly cloudy (night)",
    3: "Partly cloudy (day)", 4: "Not used", 5: "Mist",
    6: "Fog", 7: "Cloudy", 8: "Overcast",
    9: "Light rain shower (night)", 10: "Light rain shower (day)",
    11: "Drizzle", 12: "Light rain", 13: "Heavy rain shower (night)",
    14: "Heavy rain shower (day)", 15: "Heavy rain",
    16: "Sleet shower (night)", 17: "Sleet shower (day)", 18: "Sleet",
    19: "Hail shower (night)", 20: "Hail shower (day)", 21: "Hail",
    22: "Light snow shower (night)", 23: "Light snow shower (day)",
    24: "Light snow", 25: "Heavy snow shower (night)",
    26: "Heavy snow shower (day)", 27: "Heavy snow",
    28: "Thunder shower (night)", 29: "Thunder shower (day)",
    30: "Thunder",
}

from scraper.base import BaseScraper, EvidenceItem

class MetOfficeScraper(BaseScraper):
    domain = "weather"
    source_name = "Met Office"
    source_url = SOURCE_URL

    def __init__(
        self,
        max_items: int = 10,
        location_id: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
    ) -> None:
        super().__init__(max_items)
        self._location_id = location_id  # unused in current DataPoint path (uses lat/lon)
        self._lat = lat if lat is not None else NEWCASTLE_LAT
        self._lon = lon if lon is not None else NEWCASTLE_LON

    def scrape(self) -> list[EvidenceItem]:
        api_key = os.environ.get("METOFFICE_API_KEY", "").strip()
        if api_key:
            logger.info("Met Office: using DataPoint API")
            items = self._scrape_api(api_key)
            if items:
                return items
            logger.warning("Met Office DataPoint returned no items; falling back to Playwright")

        logger.info("Met Office: using Playwright fallback")
        return self._scrape_playwright()

    def _scrape_api(self, api_key: str) -> list[EvidenceItem]:
        import requests

        try:
            resp = requests.get(
                DATAPOINT_URL,
                params={
                    "latitude": self._lat,
                    "longitude": self._lon,
                    "includeLocationName": "true",
                },
                headers={
                    "apikey": api_key,
                    "accept": "application/json",
                    "User-Agent": "Mozilla/5.0 (research scraper; neet dataset project)",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("Met Office API error: %s", exc)
            return []

        items: list[EvidenceItem] = []

        try:
            features = data.get("features", [])
            if not features:
                return []
            properties = features[0].get("properties", {})
            time_series = properties.get("timeSeries", [])
        except (KeyError, IndexError):
            logger.error("Unexpected DataPoint response structure")
            return []

        for entry in time_series:
            if len(items) >= self.max_items:
                break

            time_str = entry.get("time", "")
            temp = entry.get("screenTemperature")
            wx_code = entry.get("significantWeatherCode")
            wind_speed = entry.get("windSpeed10m")
            precip_prob = entry.get("probOfPrecipitation")

            if not time_str:
                continue

            condition = WX_CODES.get(wx_code, "Unknown conditions") if wx_code is not None else "Variable conditions"

            parts = [f"Met Office forecast for Newcastle upon Tyne: {condition}"]
            if temp is not None:
                parts.append(f"{temp}°C")
            if wind_speed is not None:
                parts.append(f"wind {wind_speed} mph")
            if precip_prob is not None:
                parts.append(f"{precip_prob}% chance of precipitation")
            parts.append(f"at {time_str}.")

            evidence = ", ".join(parts[:-1]) + " " + parts[-1]

            items.append(EvidenceItem(
                item_id=self._next_id(),
                domain=self.domain,
                evidence_text=evidence,
                source_name=self.source_name,
                source_page="Newcastle upon Tyne Hourly Forecast",
                source_url=SOURCE_URL,
                date_time=time_str,
                location="Newcastle upon Tyne",
            ))

        logger.info("Met Office API: collected %d items", len(items))
        return items

    def _scrape_playwright(self) -> list[EvidenceItem]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
            return []

        scraped_at = datetime.now(timezone.utc).isoformat()
        items: list[EvidenceItem] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(SOURCE_URL, wait_until="networkidle", timeout=30000)

                # Met Office uses <li> items with data-* attributes for each period
                selectors = [
                    "[data-time]",
                    ".forecast-period",
                    ".day-forecast",
                    "li[class*='forecast']",
                    "tr[class*='forecast']",
                ]

                rows = []
                for sel in selectors:
                    rows = page.query_selector_all(sel)
                    if rows:
                        logger.info("Met Office Playwright: found %d rows with selector '%s'", len(rows), sel)
                        break

                if not rows:
                    content = page.inner_text("main") or page.inner_text("body")
                    lines = [ln.strip() for ln in content.splitlines() if ln.strip() and len(ln.strip()) > 20]
                    for line in lines[:self.max_items]:
                        if any(kw in line.lower() for kw in ("°c", "rain", "cloud", "wind", "sun", "snow", "fog", "shower")):
                            items.append(EvidenceItem(
                                item_id=self._next_id(),
                                domain=self.domain,
                                evidence_text=f"Met Office Newcastle forecast: {line}",
                                source_name=self.source_name,
                                source_page="Newcastle upon Tyne Forecast",
                                source_url=SOURCE_URL,
                                date_time=scraped_at,
                                location="Newcastle upon Tyne",
                            ))
                    logger.info("Met Office Playwright text fallback: %d items", len(items))
                    return items

                for row in rows[:self.max_items]:
                    text = row.inner_text().strip().replace("\n", " ")
                    if not text or len(text) < 10:
                        continue
                    items.append(EvidenceItem(
                        item_id=self._next_id(),
                        domain=self.domain,
                        evidence_text=f"Met Office Newcastle forecast: {text}",
                        source_name=self.source_name,
                        source_page="Newcastle upon Tyne Forecast",
                        source_url=SOURCE_URL,
                        date_time=scraped_at,
                        location="Newcastle upon Tyne",
                    ))

            finally:
                browser.close()

        logger.info("Met Office Playwright: collected %d items", len(items))
        return items

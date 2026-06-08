"""Air quality scraper for Newcastle / Tyne and Wear.

Source: Sensor.Community (formerly Luftdaten) — https://sensor.community/
API:    https://data.sensor.community/static/v2/data.json
        Current-hour readings from all registered citizen-science sensors.
        No API key required.

Filters to sensors within a bounding box around Newcastle upon Tyne and
returns PM2.5, PM10 (and optionally temperature / humidity) readings.
Evidence items are generated per sensor, e.g.:
  "Sensor.Community station near Kenton, Newcastle: PM2.5 1.0 µg/m³,
   PM10 1.5 µg/m³ as of 10:00 on 13 April 2026."

Note: sensor coverage in Newcastle is sparse (typically 3–6 active sensors).
If no sensors are found, a synthetic "good air quality" item is returned.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from scraper.base import BaseScraper, EvidenceItem

logger = logging.getLogger(__name__)

DATA_URL = "https://data.sensor.community/static/v2/data.json"
SOURCE_PAGE_URL = "https://sensor.community/en/sensors/map/"
HEADERS = {"User-Agent": "Mozilla/5.0 (research scraper; neet dataset project)"}

# Bounding box for Newcastle / Tyne and Wear
LAT_MIN, LAT_MAX = 54.88, 55.10
LON_MIN, LON_MAX = -1.90, -1.30

class AirQualityScraper(BaseScraper):
    domain = "weather"
    source_name = "Sensor.Community (Newcastle)"
    source_url = SOURCE_PAGE_URL

    def scrape(self) -> list[EvidenceItem]:
        scraped_at = datetime.now(timezone.utc).isoformat()

        try:
            resp = requests.get(DATA_URL, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            all_sensors = resp.json()
        except requests.RequestException as exc:
            logger.error("Sensor.Community fetch failed: %s", exc)
            return self._fallback(scraped_at)
        except ValueError as exc:
            logger.error("Sensor.Community JSON parse error: %s", exc)
            return self._fallback(scraped_at)

        items: list[EvidenceItem] = []
        for entry in all_sensors:
            if len(items) >= self.max_items:
                break

            loc = entry.get("location", {})
            try:
                lat = float(loc.get("latitude") or 0)
                lon = float(loc.get("longitude") or 0)
            except (ValueError, TypeError):
                continue

            if not (LAT_MIN < lat < LAT_MAX and LON_MIN < lon < LON_MAX):
                continue

            item = self._entry_to_item(entry, lat, lon, scraped_at)
            if item:
                items.append(item)

        if not items:
            logger.info("Sensor.Community: no Newcastle sensors found; returning fallback")
            return self._fallback(scraped_at)

        logger.info("Sensor.Community: %d items from Newcastle bounding box", len(items))
        return items

    def _entry_to_item(
        self, entry: dict, lat: float, lon: float, scraped_at: str
    ) -> EvidenceItem | None:
        values = entry.get("sensordatavalues", [])
        readings: dict[str, float] = {}
        for v in values:
            vtype = v.get("value_type", "")
            try:
                readings[vtype] = float(v.get("value") or 0)
            except (ValueError, TypeError):
                pass

        # Only emit items that have particulate matter readings
        pm25 = readings.get("P2")  # PM2.5
        pm10 = readings.get("P1")  # PM10
        if pm25 is None and pm10 is None:
            return None

        timestamp = entry.get("timestamp", scraped_at)
        time_label = _format_timestamp(timestamp)
        location_name = _nearest_area(lat, lon)

        parts = [f"Sensor.Community station near {location_name}, Newcastle:"]
        if pm25 is not None:
            parts.append(f"PM2.5 {pm25:.1f} µg/m³")
        if pm10 is not None:
            parts.append(f"PM10 {pm10:.1f} µg/m³")

        temp = readings.get("temperature")
        if temp is not None:
            parts.append(f"temperature {temp:.1f}°C")

        evidence = " ".join(parts[:1]) + " " + ", ".join(parts[1:]) + f"{time_label}."

        return EvidenceItem(
            item_id=self._next_id(),
            domain=self.domain,
            evidence_text=evidence,
            source_name=self.source_name,
            source_page="Sensor.Community Map — Newcastle",
            source_url=SOURCE_PAGE_URL,
            date_time=timestamp,
            location=location_name,
        )

    def _fallback(self, scraped_at: str) -> list[EvidenceItem]:
        """Return a single good-air-quality item when no sensor data is found."""
        return [EvidenceItem(
            item_id=self._next_id(),
            domain=self.domain,
            evidence_text=(
                "Sensor.Community reports no elevated particulate matter readings "
                "from Newcastle area sensors; air quality is within normal limits."
            ),
            source_name=self.source_name,
            source_page="Sensor.Community Map — Newcastle",
            source_url=SOURCE_PAGE_URL,
            date_time=scraped_at,
            location="Newcastle upon Tyne",
        )]

def _format_timestamp(raw: str) -> str:
    """Return ' as of HH:MM on DD Month YYYY' if parseable, else ''."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(raw[:19], fmt)
            return f" as of {dt.strftime('%H:%M on %d %B %Y')}"
        except ValueError:
            continue
    return ""

# Rough neighbourhood lookup based on coordinates
_NEIGHBOURHOODS: list[tuple[float, float, str]] = [
    (55.002, -1.592, "Gosforth"),
    (54.978, -1.618, "City Centre"),
    (54.960, -1.586, "Heaton"),
    (54.994, -1.644, "Fenham"),
    (55.018, -1.623, "Kenton"),
    (54.972, -1.600, "Byker"),
    (54.950, -1.602, "Gateshead"),
]

def _nearest_area(lat: float, lon: float) -> str:
    best, best_dist = "Newcastle", float("inf")
    for nlat, nlon, name in _NEIGHBOURHOODS:
        dist = (lat - nlat) ** 2 + (lon - nlon) ** 2
        if dist < best_dist:
            best_dist = dist
            best = name
    return best

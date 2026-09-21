"""Newcastle City Council roadworks scraper.

Source: https://community.newcastle.gov.uk/apps/showroadworks
The map is powered by One.Network (organisation_id=1185, site_code=D120RY7Z47).
The roadworks data is delivered as Mapbox Vector Tiles (PBF format).

Three-stage approach:
  1. One.Network PBF tiles: fetch vector tiles for Newcastle area and decode them.
     Requires mapbox-vector-tile. Tiles are empty when there are no active works.
  2. Playwright: load the map and intercept JSON API responses.
  3. Council news fallback: call CouncilNewsScraper filtered for roadworks keywords.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from scraper.base import BaseScraper, EvidenceItem

logger = logging.getLogger(__name__)

MAP_URL = "https://community.newcastle.gov.uk/apps/showroadworks"

# Keywords that suggest a JSON response contains roadworks data
ROADWORKS_JSON_HINTS = ("roadwork", "closure", "restriction", "works", "diversion",
                        "traffic", "lane", "description", "location")

_DEFAULT_TILES = [
    (507, 323), (507, 324),
    (506, 323), (506, 324),
    (508, 323), (508, 324),
]

import math as _math


def tile_geometry_to_wgs84(geometry, z: int, x: int, y: int, extent: int = 4096):
    """Convert a decoded Mapbox-vector-tile geometry (tile-local integer coordinates, origin bottom-left as
    `mapbox_vector_tile.decode` returns by default) into WGS84 GeoJSON for tile z/x/y."""
    if not isinstance(geometry, dict) or "coordinates" not in geometry:
        return None
    n = 2 ** z

    def conv(pt):
        px, py = float(pt[0]), float(pt[1])
        lon = (x + px / extent) / n * 360.0 - 180.0
        lat_rad = _math.atan(_math.sinh(_math.pi * (1 - 2 * (y + (extent - py) / extent) / n)))
        return [round(lon, 6), round(_math.degrees(lat_rad), 6)]

    def walk(coords, depth):
        if depth == 0:
            return conv(coords)
        return [walk(c, depth - 1) for c in coords]

    depth = {"Point": 0, "MultiPoint": 1, "LineString": 1, "MultiLineString": 2, "Polygon": 2, "MultiPolygon": 3}.get(geometry.get("type"))
    if depth is None:
        return None
    try:
        return {"type": geometry["type"], "coordinates": walk(geometry["coordinates"], depth)}
    except (TypeError, IndexError, ValueError):
        return None


class RoadworksScraper(BaseScraper):
    domain = "roadworks"
    source_name = "Newcastle City Council Roadworks"
    source_url = MAP_URL

    def __init__(
        self,
        max_items: int = 10,
        tile_zoom: int | None = None,
        tiles: list | None = None,
        organisation_id: str | None = None,
        date_range_days_past: int | None = None,
        date_range_days_future: int | None = None,
    ) -> None:
        super().__init__(max_items)
        self._tile_zoom = tile_zoom or 10
        self._tiles = [tuple(t) for t in tiles] if tiles else _DEFAULT_TILES
        self._organisation_id = organisation_id or "1185"
        self._days_past = date_range_days_past if date_range_days_past is not None else 1
        self._days_future = date_range_days_future if date_range_days_future is not None else 90

    def scrape(self) -> list[EvidenceItem]:
        try:
            items = self._scrape_one_network_tiles()
            if items:
                return items
            logger.info("Roadworks: One.Network tiles returned no items (may be empty today)")
        except Exception as exc:
            logger.warning("Roadworks: One.Network tiles failed (%s)", exc)

        try:
            items = self._scrape_playwright()
            if items:
                return items
            logger.warning("Roadworks: Playwright returned no items; trying council news fallback")
        except Exception as exc:
            logger.warning("Roadworks: Playwright failed (%s); trying council news fallback", exc)

        return self._council_news_fallback()

    ONE_NETWORK_TILE_URL = (
        "https://eu2-prd-pg-ts1.one.network"
        "/tileserv.maplayer_roadworks/{z}/{x}/{y}.pbf"
    )

    def _tile_request(self, start_date: str, end_date: str) -> tuple[dict, dict]:
        filters = json.dumps({
            "impact": ["-1", "0", "1", "2", "3", "4"],
            "works_state": ["-1", "0", "2", "3", "4", "5", "6", "8"],
            "permit_status": ["-1", "0", "101", "11", "12", "13", "14",
                              "25", "26", "27", "28", "29", "30", "4"],
            "ttro_state": ["-100", "4", "5"],
        }, separators=(",", ":"))

        params = {
            "predefineddates": "true",
            "startdate": start_date,
            "enddate": end_date,
            "tz": "Europe/London",
            "publicuser": "0",
            "organisationid": self._organisation_id,
            "extendedfunctionid": "14",
            "ownworksflag": "0",
            "tmshowunpublished": "0",
            "tags": "notags",
            "lang": "en-GB",
            "filters": filters,
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://community.newcastle.gov.uk/",
            "Accept": "*/*",
        }
        return params, headers

    def _scrape_one_network_tiles(self) -> list[EvidenceItem]:
        try:
            import mapbox_vector_tile
        except ImportError:
            logger.warning("mapbox-vector-tile not installed; skipping tile stage")
            return []

        import requests

        scraped_at = datetime.now(timezone.utc)

        start_dt = scraped_at.replace(hour=23, minute=0, second=0, microsecond=0) - timedelta(days=self._days_past)
        end_dt = scraped_at.replace(hour=22, minute=59, second=59, microsecond=0) + timedelta(days=self._days_future)
        start_date = start_dt.strftime("%d/%m/%Y %H:%M:%S")
        end_date = end_dt.strftime("%d/%m/%Y %H:%M:%S")

        params, headers = self._tile_request(start_date, end_date)

        items: list[EvidenceItem] = []
        for props in self._iter_tile_features(params, headers):
            if len(items) >= self.max_items:
                break
            item = self._tile_feature_to_item(props, scraped_at.isoformat())
            if item:
                items.append(item)
        logger.info("Roadworks One.Network tiles: %d items from %d tiles", len(items), len(self._tiles))
        return items

    def _iter_tile_features(self, params: dict, headers: dict):
        """Yield raw feature property dicts from every configured tile (deduplicated by id/work_ref/usrn)."""
        import mapbox_vector_tile
        import requests

        z = self._tile_zoom
        seen_ids: set[str] = set()
        for x, y in self._tiles:
            url = self.ONE_NETWORK_TILE_URL.format(z=z, x=x, y=y)
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=15)
                if not resp.ok or not resp.content:
                    logger.debug("Tile %s/%s/%s: status=%s size=%s", z, x, y, resp.status_code, len(resp.content))
                    continue
                tile = mapbox_vector_tile.decode(resp.content)
                for layer_name, layer in tile.items():
                    extent = int(layer.get("extent") or 4096)
                    for feat in layer.get("features", []):
                        props = dict(feat.get("properties", {}))
                        uid = str(props.get("id") or props.get("work_ref") or props.get("usrn", ""))
                        if uid and uid in seen_ids:
                            continue
                        if uid:
                            seen_ids.add(uid)
                        props["_tile"] = f"{z}/{x}/{y}"
                        props["_layer"] = layer_name
                        # Keep the works geometry (WS1): tile-local coordinates -> WGS84 GeoJSON.
                        geom = tile_geometry_to_wgs84(feat.get("geometry"), z, x, y, extent)
                        if geom is not None:
                            props["_geometry"] = geom
                        yield props
            except Exception as exc:
                logger.debug("Tile %s/%s/%s fetch error: %s", z, x, y, exc)

    def fetch_tile_features(self) -> list[dict]:
        """Raw one.network feature properties for the configured window (used by the capture job)."""
        from datetime import datetime, timezone, timedelta
        scraped_at = datetime.now(timezone.utc)
        start_dt = scraped_at.replace(hour=23, minute=0, second=0, microsecond=0) - timedelta(days=self._days_past)
        end_dt = scraped_at.replace(hour=22, minute=59, second=59, microsecond=0) + timedelta(days=self._days_future)
        params, headers = self._tile_request(start_dt.strftime("%d/%m/%Y %H:%M:%S"), end_dt.strftime("%d/%m/%Y %H:%M:%S"))
        return list(self._iter_tile_features(params, headers))

    IMPACT_LABELS = {
        "0": "no delay", "1": "delays unlikely", "2": "delays possible",
        "3": "delays likely", "4": "severe delays",
    }

    def _tile_feature_to_item(self, props: dict, scraped_at: str) -> "EvidenceItem | None":
        # One.Network feature fields (confirmed from live tile data)
        works_desc = str(props.get("works_desc", "")).strip()
        road_name = str(props.get("road_name", "")).strip()
        traffman = str(props.get("traffman", "")).strip()
        delay = str(props.get("delay", "")).strip()
        start_date = str(props.get("start_date", scraped_at)).strip()
        end_date = str(props.get("end_date", "")).strip()
        pub_name = str(props.get("pub_name", "")).strip()

        if not works_desc or len(works_desc) < 10:
            return None
        if not road_name:
            return None

        parts = [f"Roadworks on {road_name}: {works_desc}."]
        if traffman and traffman.lower() not in ("no carriageway incursion",):
            parts.append(f"Traffic management: {traffman}.")
        if delay:
            parts.append(f"{delay.capitalize()}.")
        if end_date:
            end_str = end_date[:10]
            parts.append(f"Work expected to end {end_str}.")

        evidence = " ".join(parts)
        location = f"{road_name}, {pub_name}" if pub_name else road_name

        return EvidenceItem(
            item_id=self._next_id(),
            domain=self.domain,
            evidence_text=evidence[:350],
            source_name=self.source_name,
            source_page="Roadworks in Newcastle Map (One.Network)",
            source_url=MAP_URL,
            date_time=start_date,
            location=road_name,
        )

    def _scrape_playwright(self) -> list[EvidenceItem]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright not installed")
            return []

        scraped_at = datetime.now(timezone.utc).isoformat()
        captured: list[dict] = []

        def handle_response(response):
            if len(captured) >= 50:
                return
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type and "geojson" not in content_type:
                return
            try:
                body = response.json()
                text = json.dumps(body).lower()
                if any(hint in text for hint in ROADWORKS_JSON_HINTS):
                    captured.append(body)
            except Exception:
                pass

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                page = context.new_page()
                page.on("response", handle_response)
                # Use "load" (not "networkidle") — the Leaflet map keeps fetching
                # tiles indefinitely, so networkidle never fires.
                page.goto(MAP_URL, wait_until="load", timeout=30000)
                page.wait_for_timeout(5000)
            finally:
                browser.close()

        items = self._parse_captured_json(captured, scraped_at)
        if items:
            logger.info("Roadworks Playwright stage 1: %d items from API intercept", len(items))
            return items

        logger.info("Roadworks: no API responses captured; no DOM items found")
        return []

    def _parse_captured_json(self, responses: list[dict], scraped_at: str) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []

        for body in responses:
            if len(items) >= self.max_items:
                break

            if body.get("type") == "FeatureCollection":
                for feature in body.get("features", []):
                    if len(items) >= self.max_items:
                        break
                    props = feature.get("properties", {})
                    item = self._props_to_item(props, scraped_at)
                    if item:
                        items.append(item)
                continue

            if isinstance(body, list):
                for entry in body:
                    if len(items) >= self.max_items:
                        break
                    if isinstance(entry, dict):
                        item = self._props_to_item(entry, scraped_at)
                        if item:
                            items.append(item)
                continue

            if isinstance(body, dict):
                for key in ("results", "data", "items", "roadworks", "features"):
                    sub = body.get(key)
                    if isinstance(sub, list):
                        for entry in sub:
                            if len(items) >= self.max_items:
                                break
                            if isinstance(entry, dict):
                                item = self._props_to_item(entry, scraped_at)
                                if item:
                                    items.append(item)
                        break

        return items

    def _props_to_item(self, props: dict, scraped_at: str) -> EvidenceItem | None:
        description_keys = ("description", "works_description", "comments",
                            "comment", "detail", "title", "name", "summary")
        location_keys = ("street_name", "street", "location", "road_name",
                         "usrn_street", "address", "place")
        date_keys = ("start_date", "date_start", "from_date", "expectedStart",
                     "startDate", "date")

        description = next((str(props[k]) for k in description_keys if props.get(k)), "")
        location = next((str(props[k]) for k in location_keys if props.get(k)), "Newcastle upon Tyne")
        date_val = next((str(props[k]) for k in date_keys if props.get(k)), scraped_at)

        if not description or len(description) < 10:
            return None

        evidence = f"Roadworks on {location}: {description}" if location != "Newcastle upon Tyne" else description

        return EvidenceItem(
            item_id=self._next_id(),
            domain=self.domain,
            evidence_text=evidence,
            source_name=self.source_name,
            source_page="Roadworks in Newcastle Map",
            source_url=MAP_URL,
            date_time=date_val,
            location=location,
        )

    def _council_news_fallback(self) -> list[EvidenceItem]:
        from scraper.scrapers.council_news import CouncilNewsScraper, ROADWORKS_KEYWORDS

        scraper = CouncilNewsScraper(
            max_items=self.max_items,
            keywords=ROADWORKS_KEYWORDS,
            domain=self.domain,
        )
        items = scraper.scrape()
        # Re-ID items to use roadworks_ prefix
        for i, item in enumerate(items, start=self._counter + 1):
            item.item_id = f"{self.domain}_{i:03d}"
            self._counter = i

        if items:
            logger.info("Roadworks: council news fallback returned %d items", len(items))
        else:
            logger.warning("Roadworks: all stages failed — no items collected")
        return items

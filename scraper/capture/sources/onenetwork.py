"""one.network roadworks tiles (as used by the existing RoadworksScraper) — daily raw capture of feature properties
including the promoter-authored `works_desc` free text. Terms risk noted in the plan; internal research use."""
from __future__ import annotations

import json

from ..base import CaptureSource, Context
from ..store import Payload


class OneNetworkSource(CaptureSource):
    name = "onenetwork"
    cadence_s = 24 * 3600

    def fetch(self, ctx: Context) -> list[Payload]:
        from scraper.scrapers.roadworks import RoadworksScraper  # reuse tile logic
        scraper = RoadworksScraper(max_items=10000)
        feats = []
        fetch_raw = getattr(scraper, "fetch_tile_features", None)
        if fetch_raw is None:
            # fall back to the item-level scrape if raw access is unavailable
            items = scraper.scrape()
            feats = [i.__dict__ for i in items]
        else:
            feats = fetch_raw()
        body = json.dumps({"features": feats}, ensure_ascii=False).encode("utf-8")
        return [Payload(tag="tiles", body=body, ext="json", meta={"note": "one.network vector tiles, raw properties"}, n_items=len(feats))]

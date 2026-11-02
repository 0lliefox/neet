"""one.network roadworks tiles (as used by the existing RoadworksScraper) — daily raw capture of feature properties
including the promoter-authored `works_desc` free text. Terms risk noted in the plan; internal research use.

The tile loop skips tiles it cannot fetch, so an outage looks like a smaller (or empty) map. A run in which any
tile failed, or whose feature count falls below `min_ratio` (default 0.5) of the median of the last good runs,
still stores what it got but is marked degraded: the health record says why and the run counts as failed, so it
retries and shows in the status report. A run with no features at all is a failure, and any failed run is a
status problem at once (`alert_on_failure`): a daily source would otherwise wait two days to alert."""
from __future__ import annotations

import json
from statistics import median

from ..base import CaptureSource, Context
from ..store import Payload

RECENT_RUNS = 7


class OneNetworkSource(CaptureSource):
    name = "onenetwork"
    cadence_s = 24 * 3600
    alert_on_failure = True

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
        stats = getattr(scraper, "tile_stats", None) or {}
        tiles, failed = stats.get("tiles"), stats.get("failed", 0)
        if not feats:
            # a real day has ~4000
            detail = f"{failed}/{tiles} tiles failed: {stats['errors'][0]}" if failed else "every tile empty"
            raise RuntimeError(f"no features from any tile ({detail})")
        recent = [int(n) for n in ctx.state.get("recent_n_features") or []]
        normal = median(recent) if recent else None
        problems = []
        if failed:
            problems.append(f"{failed}/{tiles} tiles failed")
        if normal and len(feats) < float(self.cfg.get("min_ratio", 0.5)) * normal:
            problems.append(f"{len(feats)} features, normal ~{normal:.0f}")
        meta = {"note": "one.network vector tiles, raw properties", "tiles": tiles, "tiles_failed": failed}
        if stats.get("errors"):
            meta["tile_errors"] = stats["errors"][:5]
        if problems:
            meta["degraded"] = "; ".join(problems)
        else:
            ctx.state.set("recent_n_features", (recent + [len(feats)])[-RECENT_RUNS:])
        body = json.dumps({"features": feats}, ensure_ascii=False).encode("utf-8")
        return [Payload(tag="tiles", body=body, ext="json", meta=meta, n_items=len(feats))]

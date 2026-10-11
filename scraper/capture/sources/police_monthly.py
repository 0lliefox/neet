"""Police.uk street-level crime around each configured point, once per month (data lags ~2 months)."""
from __future__ import annotations

import time
from datetime import timedelta

from ..base import CaptureSource, Context
from ..store import Payload, utcnow


class PoliceMonthlySource(CaptureSource):
    name = "police"
    cadence_s = 24 * 3600

    def _get(self, ctx: Context, params: dict):
        # Police.uk rate-limits bursts (HTTP 429): pause between calls, and back off and retry a 429 a few times.
        # `pause_s` / `retry_s` come from config so tests can set them to 0.
        pause, retry = float(self.cfg.get("pause_s", 1.0)), float(self.cfg.get("retry_s", 10.0))
        for attempt in range(4):
            time.sleep(pause)
            r = self.get(ctx, "https://data.police.uk/api/crimes-street/all-crime", params=params, timeout=120)
            if r.status_code != 429:
                return r
            try:
                wait = float(r.headers.get("Retry-After", ""))
            except ValueError:
                wait = retry * (attempt + 1)
            time.sleep(min(wait, 60.0))
        return r

    def fetch(self, ctx: Context) -> list[Payload]:
        # Done months are tracked per point, so a point added later still backfills the months the others already
        # have, and one point's success never marks the month done for the rest. The old shared `months_done` key is
        # left in state untouched; the first run after the change re-fetches the last four months once, and the
        # store's hash de-dup drops the unchanged bodies.
        done: dict[str, list[str]] = ctx.state.get("months_done_by_point", {})
        out = []
        now = utcnow()
        for back in range(1, 5):  # try the last 4 months; API returns 404 until a month is published
            m = (now.replace(day=1) - timedelta(days=28 * back)).strftime("%Y-%m")
            for point in self.cfg.get("points", [{"lat": 54.9781, "lng": -1.6162, "name": "newcastle_centre"}]):
                if m in done.get(point["name"], []):
                    continue
                r = self._get(ctx, {"lat": point["lat"], "lng": point["lng"], "date": m})
                if r.status_code == 404:
                    continue
                n = len(r.json()) if r.ok else None
                out.append(self.payload_from_response(f"{point['name']}_{m}", r, ext="json", n_items=n))
                if r.ok and n:
                    done.setdefault(point["name"], []).append(m)  # an empty list means not published yet: retry tomorrow
        ctx.state.set("months_done_by_point", {k: sorted(set(v))[-24:] for k, v in done.items()})
        return out

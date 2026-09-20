"""Police.uk street-level crime for the Newcastle area, once per month (data lags ~2 months)."""
from __future__ import annotations

from datetime import timedelta

from ..base import CaptureSource, Context
from ..store import Payload, utcnow


class PoliceMonthlySource(CaptureSource):
    name = "police"
    cadence_s = 24 * 3600

    def fetch(self, ctx: Context) -> list[Payload]:
        done: list[str] = ctx.state.get("months_done", [])
        out = []
        now = utcnow()
        for back in range(1, 5):  # try the last 4 months; API returns 404 until a month is published
            m = (now.replace(day=1) - timedelta(days=28 * back)).strftime("%Y-%m")
            if m in done:
                continue
            for point in self.cfg.get("points", [{"lat": 54.9781, "lng": -1.6162, "name": "newcastle_centre"}]):
                r = self.get(ctx, "https://data.police.uk/api/crimes-street/all-crime",
                             params={"lat": point["lat"], "lng": point["lng"], "date": m}, timeout=120)
                if r.status_code == 404:
                    continue
                n = len(r.json()) if r.ok else None
                out.append(self.payload_from_response(f"{point['name']}_{m}", r, ext="json", n_items=n))
                if r.ok and n:
                    done.append(m)  # an empty list means the month is not published yet: retry tomorrow
        ctx.state.set("months_done", sorted(set(done))[-24:])
        return out

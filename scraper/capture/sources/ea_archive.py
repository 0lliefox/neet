"""EA daily archive CSV (readings-full-YYYY-MM-DD.csv, all stations nationally, ~tens of MB). Pulled once per day
for the previous day; the whole file is kept (it is the reproducible record)."""
from __future__ import annotations

from datetime import timedelta

from ..base import CaptureSource, Context
from ..store import Payload, utcnow


class EAArchiveSource(CaptureSource):
    name = "ea_archive"
    cadence_s = 6 * 3600  # checks 4x/day; downloads only the days not yet present

    def fetch(self, ctx: Context) -> list[Payload]:
        done: list[str] = ctx.state.get("days_done", [])
        out = []
        today = utcnow().date()
        for back in range(1, self.cfg.get("lookback_days", 3) + 1):
            day = (today - timedelta(days=back)).isoformat()
            if day in done:
                continue
            r = self.get(ctx, f"https://environment.data.gov.uk/flood-monitoring/archive/readings-full-{day}.csv", timeout=300)
            if r.ok and r.content:
                out.append(Payload(tag=f"readings_full_{day}", body=r.content, ext="csv",
                                   meta={"url": r.url, "status": r.status_code}, n_items=r.content.count(b"\n")))
                done.append(day)
        ctx.state.set("days_done", done[-60:])
        return out

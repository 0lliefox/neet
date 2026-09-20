"""Urban Observatory v2 API (keyless): daily backfill of the previous day's readings for selected variables inside a
Tyne-and-Wear bbox, plus a weekly sensor inventory. Paginates; tolerates 504 on heavy variables."""
from __future__ import annotations

from datetime import timedelta

from ..base import CaptureSource, Context
from ..store import Payload, utcnow

BASE = "https://api.v2.urbanobservatory.ac.uk"
DEFAULT_VARIABLES = ["Journey Time", "NO2", "Temperature", "Humidity", "Wind Speed", "Occupied spaces", "PM2.5", "Rainfall", "Traffic Flow"]
DEFAULT_BBOX = {"bbox_p1_x": -1.80, "bbox_p1_y": 54.90, "bbox_p2_x": -1.35, "bbox_p2_y": 55.08}


class UOSource(CaptureSource):
    name = "uo"
    cadence_s = 6 * 3600

    def fetch(self, ctx: Context) -> list[Payload]:
        out: list[Payload] = []
        bbox = self.cfg.get("bbox", DEFAULT_BBOX)
        base = self.cfg.get("base_url", BASE)
        # weekly inventory
        last_inv = ctx.state.get("inventory_at")
        if not last_inv or (utcnow() - utcnow().fromisoformat(last_inv)) > timedelta(days=7):
            r = self.get(ctx, f"{base}/sensors/json", params={**bbox, "limit": 10000}, timeout=120)
            out.append(self.payload_from_response("sensors_inventory", r, ext="json"))
            if r.ok:
                ctx.state.set("inventory_at", utcnow().isoformat())
        # daily backfill of previous days not yet done, per variable
        done: dict[str, list[str]] = ctx.state.get("days_done", {})
        today = utcnow().date()
        for var in self.cfg.get("variables", DEFAULT_VARIABLES):
            days = done.setdefault(var, [])
            for back in range(1, self.cfg.get("lookback_days", 2) + 1):
                day = (today - timedelta(days=back)).isoformat()
                if day in days:
                    continue
                ok_all = True
                offset, limit, page = 0, 5000, 0
                while True:
                    r = self.get(ctx, f"{base}/sensors/data/json", params={**bbox, "start": f"{day}T00:00:00", "end": f"{day}T23:59:59",
                                                                          "variables": var, "limit": limit, "offset": offset}, timeout=180)
                    n = None
                    if r.ok:
                        try:
                            n = len(r.json().get("Readings", []))
                        except ValueError:
                            n = None
                    out.append(self.payload_from_response(f"{var}_{day}_p{page}", r, ext="json", n_items=n,
                                                          extra_meta={"variable": var, "day": day, "offset": offset}))
                    if not r.ok:
                        ok_all = False
                        break
                    if n is None or n < limit:
                        break
                    offset += limit
                    page += 1
                    if page > 40:
                        break
                if ok_all:
                    days.append(day)
        ctx.state.set("days_done", {k: v[-45:] for k, v in done.items()})
        return out

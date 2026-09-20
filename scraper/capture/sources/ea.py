"""Environment Agency flood-monitoring API (keyless, OGL): current floods for Tyne and Wear / Northumberland,
rainfall + level stations within `dist_km` of Newcastle, and their latest readings. The API is slow and returns
502 intermittently; each call is independent so partial results are kept."""
from __future__ import annotations

from ..base import CaptureSource, Context
from datetime import timedelta

from ..store import Payload, utcnow

BASE = "https://environment.data.gov.uk/flood-monitoring"


class EASource(CaptureSource):
    name = "ea"
    cadence_s = 3600

    def fetch(self, ctx: Context) -> list[Payload]:
        lat = self.cfg.get("latitude", 54.9781)
        lon = self.cfg.get("longitude", -1.6162)
        dist = self.cfg.get("dist_km", 15)
        out: list[Payload] = []
        for county in self.cfg.get("counties", ["Tyne and Wear", "Northumberland", "Durham"]):
            r = self.get(ctx, f"{BASE}/id/floods", params={"county": county}, timeout=90)
            out.append(self.payload_from_response(f"floods_{county}", r, ext="json",
                                                  n_items=len(r.json().get("items", [])) if r.ok else None))
        for param in ("rainfall", "level"):
            r = self.get(ctx, f"{BASE}/id/stations", params={"parameter": param, "lat": lat, "long": lon, "dist": dist}, timeout=90)
            out.append(self.payload_from_response(f"stations_{param}", r, ext="json",
                                                  n_items=len(r.json().get("items", [])) if r.ok else None))
            if r.ok:
                refs = [i.get("stationReference") for i in r.json().get("items", []) if i.get("stationReference")]
                ctx.state.set(f"stations_{param}", refs)
        # latest readings per nearby station (the /data/readings endpoint does not take lat/long/dist)
        since = (utcnow() - timedelta(hours=self.cfg.get("readings_hours", 3))).strftime("%Y-%m-%dT%H:%M:%SZ")
        for param in ("rainfall", "level"):
            for ref in ctx.state.get(f"stations_{param}", [])[:40]:
                r = self.get(ctx, f"{BASE}/id/stations/{ref}/readings", params={"since": since, "_sorted": "", "_limit": 500}, timeout=90)
                n = None
                if r.ok:
                    try:
                        n = len(r.json().get("items", []))
                    except ValueError:
                        n = None
                out.append(self.payload_from_response(f"readings_{param}_{ref}", r, ext="json", n_items=n,
                                                      extra_meta={"station": ref, "parameter": param, "since": since}))
        return out

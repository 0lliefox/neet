"""Met Office Weather DataHub site-specific hourly forecast for one or more points (needs METOFFICE_DATAHUB_KEY;
free tier 360 calls/day). `points: [{lat, lng, name}]` as for police; without it, the single Newcastle point."""
from __future__ import annotations

from ..base import CaptureSource, Context
from ..store import Payload

BASE = "https://data.hub.api.metoffice.gov.uk/sitespecific/v0/point"


class MetOfficeHourlySource(CaptureSource):
    name = "metoffice_hourly"
    cadence_s = 3600
    requires_env = ("METOFFICE_DATAHUB_KEY",)

    def points(self) -> list[dict]:
        if self.cfg.get("points"):
            return self.cfg["points"]
        return [{"lat": self.cfg.get("latitude", 54.9781), "lng": self.cfg.get("longitude", -1.6162), "name": "newcastle"}]

    def fetch(self, ctx: Context) -> list[Payload]:
        out = []
        for point in self.points():
            for kind in self.cfg.get("kinds", ["hourly"]):
                r = self.get(ctx, f"{self.cfg.get('base_url', BASE)}/{kind}",
                             params={"latitude": point["lat"], "longitude": point["lng"], "includeLocationName": "true"},
                             headers={"apikey": ctx.env["METOFFICE_DATAHUB_KEY"], "Accept": "application/json"})
                out.append(self.payload_from_response(f"{kind}_{point['name']}", r, ext="json"))
        return out

"""Met Office Weather DataHub site-specific hourly forecast for Newcastle (needs METOFFICE_DATAHUB_KEY; free tier 360 calls/day)."""
from __future__ import annotations

from ..base import CaptureSource, Context
from ..store import Payload

BASE = "https://data.hub.api.metoffice.gov.uk/sitespecific/v0/point"


class MetOfficeHourlySource(CaptureSource):
    name = "metoffice_hourly"
    cadence_s = 3600
    requires_env = ("METOFFICE_DATAHUB_KEY",)

    def fetch(self, ctx: Context) -> list[Payload]:
        lat = self.cfg.get("latitude", 54.9781)
        lon = self.cfg.get("longitude", -1.6162)
        out = []
        for kind in self.cfg.get("kinds", ["hourly"]):
            r = self.get(ctx, f"{self.cfg.get('base_url', BASE)}/{kind}",
                         params={"latitude": lat, "longitude": lon, "includeLocationName": "true"},
                         headers={"apikey": ctx.env["METOFFICE_DATAHUB_KEY"], "Accept": "application/json"})
            out.append(self.payload_from_response(f"{kind}_newcastle", r, ext="json"))
        return out

"""Bus Open Data Service SIRI-SX situation exchange (disruptions) for the North East (needs BODS_KEY)."""
from __future__ import annotations

from ..base import CaptureSource, Context
from ..store import Payload


class BODSSource(CaptureSource):
    name = "bods_sirisx"
    cadence_s = 600
    requires_env = ("BODS_KEY",)

    def fetch(self, ctx: Context) -> list[Payload]:
        url = self.cfg.get("url", "https://data.bus-data.dft.gov.uk/api/v1/siri-sx/")
        params = {"api_key": ctx.env["BODS_KEY"]}
        params.update(self.cfg.get("params", {}))  # e.g. adminArea codes for Tyne and Wear
        r = self.get(ctx, url, params=params, timeout=90)
        # do not persist the api key in meta
        meta = {"url": url.split("?")[0], "status": r.status_code, "content_type": r.headers.get("Content-Type", "")}
        return [Payload(tag="siri_sx", body=r.content, ext="xml", meta=meta,
                        n_items=r.text.count("<PtSituationElement>") if r.ok else None)]

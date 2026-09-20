"""Met Office NSWWS public API (needs NSWWS_KEY). Entries persist 24 h; we store the Atom feed + each issued/updated object."""
from __future__ import annotations

import re

from ..base import CaptureSource, Context
from ..store import Payload

BASE = "https://data.hub.api.metoffice.gov.uk/nswws/v1.1"  # per public API spec; override in config if different


class NSWWSSource(CaptureSource):
    name = "nswws"
    cadence_s = 1800
    requires_env = ("NSWWS_KEY",)

    def fetch(self, ctx: Context) -> list[Payload]:
        base = self.cfg.get("base_url", BASE)
        headers = {"apikey": ctx.env["NSWWS_KEY"], "Accept": "application/atom+xml, application/json"}
        r = self.get(ctx, f"{base}/objects/feed", headers=headers)
        out = [self.payload_from_response("feed", r, ext="xml")]
        seen: set[str] = set(ctx.state.get("seen_ids", []))
        new_ids: list[str] = []
        if r.ok:
            for kind, uuid in re.findall(r"/objects/(issued|updated)/([0-9a-fA-F-]{36})", r.text):
                key = f"{kind}/{uuid}"
                if key in seen:
                    continue
                rr = self.get(ctx, f"{base}/objects/{kind}/{uuid}", headers={"apikey": ctx.env["NSWWS_KEY"]})
                out.append(self.payload_from_response(f"{kind}_{uuid}", rr, ext="json"))
                if rr.ok:
                    new_ids.append(key)
        ctx.state.set("seen_ids", (list(seen) + new_ids)[-2000:])
        return out

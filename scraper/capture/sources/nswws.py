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
        # The feed links to per-warning objects (/objects/issued|updated/<uuid>/) and to a collection whose UUID is
        # stable while its content changes, so every linked object is fetched on every run (they are small);
        # duplicates are cheap and the store keeps each fetch as its own timestamped payload.
        if r.ok:
            for kind, uuid in dict.fromkeys(re.findall(r"/objects/(issued|updated)/([0-9a-fA-F-]{36})", r.text)):
                rr = self.get(ctx, f"{base}/objects/{kind}/{uuid}", headers={"apikey": ctx.env["NSWWS_KEY"]})
                n = None
                if rr.ok:
                    try:
                        n = len(rr.json().get("features", []))
                    except ValueError:
                        n = None
                out.append(self.payload_from_response(f"{kind}_{uuid}", rr, ext="json", n_items=n))
        return out

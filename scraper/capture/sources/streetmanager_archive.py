"""DfT Street Manager open-data monthly archives (public S3, OGL v3). Lists permit/activity/section_58 zips and
downloads any not yet stored (~1 GB each; kept as-is)."""
from __future__ import annotations

import re

from ..base import CaptureSource, Context
from ..store import Payload

BASE = "https://opendata.manage-roadworks.service.gov.uk/"


class StreetManagerArchiveSource(CaptureSource):
    name = "streetmanager_archive"
    cadence_s = 24 * 3600

    def fetch(self, ctx: Context) -> list[Payload]:
        done: list[str] = ctx.state.get("keys_done", [])
        out = []
        for kind in self.cfg.get("kinds", ["permit", "activity", "section_58"]):
            r = self.get(ctx, BASE, params={"list-type": "2", "delimiter": "/", "prefix": f"{kind}/"}, timeout=60)
            if not r.ok:
                continue
            keys = re.findall(r"<Key>([^<]+\.zip)</Key>", r.text)
            for key in keys:
                if key in done:
                    continue
                if key < self.cfg.get("min_key", f"{kind}/2026/01.zip"):
                    continue
                rr = self.get(ctx, BASE + key, timeout=1800)
                if rr.ok and rr.content:
                    out.append(Payload(tag=key.replace("/", "_").replace(".zip", ""), body=rr.content, ext="zip",
                                       meta={"url": rr.url, "status": rr.status_code, "key": key}))
                    done.append(key)
        ctx.state.set("keys_done", done)
        return out

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
        from datetime import datetime, timezone
        year_now = datetime.now(timezone.utc).year
        min_year = int(self.cfg.get("min_key_year", 2026))
        for kind in self.cfg.get("kinds", ["permit", "activity", "section_58"]):
            keys: list[str] = []
            for year in range(min_year, year_now + 1):
                # with delimiter=/ the bucket returns year prefixes only, so list each year prefix explicitly
                r = self.get(ctx, BASE, params={"list-type": "2", "prefix": f"{kind}/{year}/"}, timeout=60)
                if r.ok:
                    keys += re.findall(r"<Key>([^<]+\.zip)</Key>", r.text)
            for key in sorted(keys):
                if key in done:
                    continue
                rr = self.get(ctx, BASE + key, timeout=1800)
                if rr.ok and rr.content:
                    out.append(Payload(tag=key.replace("/", "_").replace(".zip", ""), body=rr.content, ext="zip",
                                       meta={"url": rr.url, "status": rr.status_code, "key": key}))
                    done.append(key)
        ctx.state.set("keys_done", done)
        return out

"""RSS feeds: Chronicle Live NE news, BBC Tyne, Newcastle City Council, NSWWS regional warnings RSS (keyless)."""
from __future__ import annotations

from ..base import CaptureSource, Context
from ..store import Payload

DEFAULT_FEEDS = {
    "chroniclelive_ne": "https://www.chroniclelive.co.uk/news/north-east-news/?service=rss",
    "bbc_tyne": "https://feeds.bbci.co.uk/news/england/tyne/rss.xml",
    "newcastle_council": "https://www.newcastle.gov.uk/rss.xml",
    "nswws_rss_ne": "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/ne",
}


class RSSSource(CaptureSource):
    name = "rss"
    cadence_s = 3600

    def fetch(self, ctx: Context) -> list[Payload]:
        feeds = self.cfg.get("feeds") or DEFAULT_FEEDS
        out = []
        for tag, url in feeds.items():
            r = self.get(ctx, url, headers={"User-Agent": "Mozilla/5.0 (research RSS reader)"}, timeout=40)
            n = r.text.count("<item>") if r.ok else None
            out.append(self.payload_from_response(tag, r, ext="xml", n_items=n))
        return out

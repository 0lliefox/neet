"""RSS headlines (Chronicle Live, BBC Tyne, council, Met Office warnings RSS): kind=headline, media reliability.
Only title + description are ever stored (Reach T&Cs); place = gazetteer names found in the text."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from ..base import Adapter, iso_utc, register
from ..geo import place_hits
from ..model import EvidenceRecord, Place

_TOPIC_DOMAIN = ("flood", "roadworks", "transport", "weather", "crime")


def domain_from_text(text: str) -> str:
    from ..geo import lexicon
    topics = (lexicon().get("topics") or {})
    low = (text or "").lower()
    best, best_n = "other", 0
    for topic in _TOPIC_DOMAIN:
        n = sum(1 for term in (topics.get(topic) or []) if str(term).lower() in low)
        if n > best_n:
            best, best_n = topic, n
    return best


class RssAdapter(Adapter):
    source = "rss"
    reliability_hint = "media"

    def records(self, body: bytes, meta: dict[str, Any]) -> list[EvidenceRecord]:
        root = ET.fromstring(body)
        out: list[EvidenceRecord] = []
        feed = str(meta.get("tag") or "rss")
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            desc = (item.findtext("description") or "").strip()
            link = (item.findtext("link") or "").strip()
            guid = (item.findtext("guid") or link or "").strip() or None
            pub = iso_utc(item.findtext("pubDate"))
            text = title if not desc else f"{title}. {desc}" if not title.endswith((".", "!", "?")) else f"{title} {desc}"
            if not text:
                continue
            hits = place_hits(text)
            hint = "official_operator" if feed.startswith("nswws") else self.reliability_hint
            out.append(EvidenceRecord(
                source=self.source, kind="headline", domain=domain_from_text(text), text=text,
                structured={"feed": feed, "title": title, "description": desc, "link": link,
                            "category": [c.text for c in item.findall("category") if c.text]},
                place=Place(name=hits[0] if hits else None, confidence=0.6 if hits else 0.0, method="lexicon" if hits else "none"),
                observed_at=pub, fetched_at=self.fetched_at(meta), valid_from=pub, valid_to=None,
                native_id=guid, reliability_hint=hint, provenance=self.provenance(meta)))
        return out


register(RssAdapter())

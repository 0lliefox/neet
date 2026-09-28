"""Reddit submissions and comments from local subreddits (official Data API): kind=post, social reliability.

The capture already dropped every author field (only `author_pid`, an HMAC of the username, survives). A post names a
place when the lexicon finds one; otherwise its local subreddit is a weak place cue (the post is about the area, not
necessarily one street). The native id is the Reddit fullname (t3_... submission, t1_... comment); the release policy
dehydrates it with the permalink."""
from __future__ import annotations

from typing import Any

from ..base import Adapter, iso_utc, load_json, register
from ..model import EvidenceRecord, Place
from .bluesky import domain_from_hits


def item_text(kind: str, item: dict[str, Any]) -> str:
    if kind == "comment":
        return str(item.get("body") or "").strip()
    return "\n".join(str(t) for t in (item.get("title"), item.get("selftext")) if t).strip()


class RedditAdapter(Adapter):
    source = "reddit"
    reliability_hint = "social"

    def records(self, body: bytes, meta: dict[str, Any]) -> list[EvidenceRecord]:
        d = load_json(body)
        sub = d.get("subreddit")
        out = []
        for entry in d.get("items") or []:
            kind, item, analysis = entry.get("kind"), entry.get("item") or {}, entry.get("analysis") or {}
            text = item_text(kind, item)
            if not text or text in ("[deleted]", "[removed]"):
                continue
            hits = analysis.get("place_hits") or {}
            strong, generic = list(hits.get("strong") or []), list(hits.get("generic") or [])
            name = strong[0] if strong else (generic[0] if generic else None)
            place = (Place(name=name, confidence=0.6 if strong else 0.3, method="lexicon") if name
                     else Place(name=f"r/{sub}", confidence=0.2, method="subreddit"))
            created = iso_utc(item.get("created_utc"))
            out.append(EvidenceRecord(
                source=self.source, kind="post", domain=domain_from_hits(analysis.get("topic_hits") or {}), text=text,
                structured={"author_pid": entry.get("author_pid"), "subreddit": sub, "item_kind": kind,
                            "topic_hits": analysis.get("topic_hits"), "place_hits": hits, "place_ok": analysis.get("place_ok"),
                            "exclusion_hits": analysis.get("exclusion_hits"), "score": item.get("score"),
                            "link_id": item.get("link_id"), "parent_id": item.get("parent_id"),
                            "permalink": item.get("permalink"), "flair": item.get("link_flair_text"),
                            "over_18": item.get("over_18"), "reply": kind == "comment"},
                place=place, observed_at=created, fetched_at=self.fetched_at(meta), valid_from=created, valid_to=None,
                native_id=item.get("name"), reliability_hint=self.reliability_hint, provenance=self.provenance(meta)))
        return out


register(RedditAdapter())

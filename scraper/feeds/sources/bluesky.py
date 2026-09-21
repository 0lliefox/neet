"""Bluesky posts captured by the keyword/author/feed strategies: kind=post, social reliability.

Pseudonymisation at the adapter boundary: the raw `post.author` object (DID, handle, display name, avatar) is
dropped; only `author_pid` (HMAC of the DID) and `author_local` survive. The AT-URI is the native id (it contains
the DID, which is public and needed to resolve the post; the release policy dehydrates it). Records that are
`dupes` in a payload are resolved by `build.py` against the sibling payloads of the same run."""
from __future__ import annotations

from typing import Any

from ..base import Adapter, iso_utc, load_json, register
from ..model import EvidenceRecord, Place

_PRIORITY = ("flood", "roadworks", "transport", "weather", "crime", "infrastructure")


def domain_from_hits(topic_hits: dict[str, Any]) -> str:
    best, best_n = "other", 0
    for topic in _PRIORITY:
        n = len(topic_hits.get(topic) or [])
        if n > best_n:
            best, best_n = topic, n
    return best if best in ("flood", "roadworks", "transport", "weather", "crime") else "other"


def post_text(post: dict[str, Any]) -> str:
    rec = post.get("record") or {}
    parts = [str(rec.get("text") or "")]
    emb = post.get("embed") or {}
    ext = emb.get("external") or {}
    for k in ("title", "description"):
        if ext.get(k):
            parts.append(str(ext[k]))
    for img in emb.get("images") or []:
        if isinstance(img, dict) and img.get("alt"):
            parts.append(str(img["alt"]))
    return "\n".join(p for p in parts if p).strip()


class BlueskyAdapter(Adapter):
    source = "bluesky"
    reliability_hint = "social"

    def records(self, body: bytes, meta: dict[str, Any]) -> list[EvidenceRecord]:
        d = load_json(body)
        out = []
        for entry in (d.get("posts") or []):
            post = entry.get("post") or {}
            analysis = entry.get("analysis") or {}
            text = post_text(post)
            if not text:
                continue
            hits = analysis.get("place_hits") or {}
            strong = list(hits.get("strong") or [])
            generic = list(hits.get("generic") or [])
            name = strong[0] if strong else (generic[0] if generic else None)
            out.append(EvidenceRecord(
                source=self.source, kind="post", domain=domain_from_hits(analysis.get("topic_hits") or {}), text=text,
                structured={"author_pid": entry.get("author_pid"), "author_local": entry.get("author_local"),
                            "strategies": entry.get("strategies"), "matched_by": entry.get("matched_by"),
                            "topic_hits": analysis.get("topic_hits"), "place_hits": hits, "place_ok": analysis.get("place_ok"),
                            "exclusion_hits": analysis.get("exclusion_hits"), "langs": (post.get("record") or {}).get("langs"),
                            "reply": bool((post.get("record") or {}).get("reply")), "cid": post.get("cid")},
                place=Place(name=name, confidence=0.6 if strong else (0.3 if generic else 0.0), method="lexicon" if name else "none"),
                observed_at=iso_utc((post.get("record") or {}).get("createdAt")), fetched_at=self.fetched_at(meta),
                valid_from=iso_utc((post.get("record") or {}).get("createdAt")), valid_to=None,
                native_id=post.get("uri"), reliability_hint=self.reliability_hint, provenance=self.provenance(meta) | {"dupes": list(d.get("dupes") or [])}))
        return out


register(BlueskyAdapter())

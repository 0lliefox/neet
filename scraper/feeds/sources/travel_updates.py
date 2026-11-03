"""travelnortheast.uk live updates (Metro/bus/ferry) scraped with Playwright: kind=notice, domain=transport.
Only the `updates` JSON payload is adapted (the raw `page` HTML is the reproducible record, not evidence).
The site gives no stable id, so the native id is a hash of title+body; `effective_from/until` are human
strings ("Mon 22 Sep, 9am") parsed best-effort into UTC instants relative to the capture year; when they cannot
be parsed, valid_from falls back to the fetch time, so record_ids are per version, not per notice.

An item is a notice across edits. Two lineage rules, chosen per build (`{"travel_updates": {"rule": ...}}`):

* `p5` (default; Oliver, 3 Nov 2026). A version is linked to an earlier one when
  (a) their bodies are identical (whitespace and case ignored), at any time: the same evidence text was already seen; or
  (b) the earlier version left the page in the capture just before this one appeared, or at most `window_h` (8) hours
      before, and the two are similar (title similarity >= 0.75 or body similarity >= 0.5, difflib ratio) and either
      keep the same title or share a place (lexicon hit, or neither has one), the page's notice type and, unless both
      are live updates, the same effective-from text.
  Status updates on an incident (suspended -> resumed) are linked by the same rule. The site reuses generic titles
  ("Service suspension") for unrelated incidents days apart, so a shared title alone does not link.
* `title_or_body` (the rule of 2 Nov): versions sharing a title or a body at any time are one notice.

DATA_DICTIONARY.md of the held-out export records the 158-pair check behind `p5`."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Optional

from ..base import Adapter, iso_utc, load_json, register
from ..geo import place_hits
from ..model import EvidenceRecord, Place

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_DATE = re.compile(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s+([A-Za-z]{3})[a-z]*\.?(?:\s+(\d{4}))?", re.I)
_TIME = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)


def parse_human_datetime(s: Optional[str], year_hint: int) -> Optional[str]:
    """'Mon 22 Sep, 9am' / '22 September 2026 09:00' / 'until further notice' -> ISO UTC or None."""
    if not s:
        return None
    iso = iso_utc(s)
    if iso:
        return iso
    m = _DATE.search(s)
    if not m:
        return None
    day, mon, year = int(m.group(1)), _MONTHS.get(m.group(2).lower()[:3]), int(m.group(3) or year_hint)
    if not mon:
        return None
    hour, minute = 0, 0
    t = _TIME.search(s)
    if t:
        hour = int(t.group(1)) % 12 + (12 if t.group(3).lower() == "pm" else 0)
        minute = int(t.group(2) or 0)
    try:
        # UK local time is treated as UTC here (±1 h in BST); civic-verifier's windows are hour-scale.
        return datetime(year, mon, day, hour, minute, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


LINK_RULES = ("p5", "title_or_body")


def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


def _rule(opts: Optional[dict]) -> str:
    rule = (opts or {}).get("rule", "p5")
    if rule not in LINK_RULES:
        raise ValueError(f"unknown travel_updates lineage rule {rule!r}; one of {LINK_RULES}")
    return rule


def _features(r: EvidenceRecord) -> dict:
    s = r.structured
    return {"title": _norm(s.get("title")), "body": _norm(s.get("body")), "type": (s.get("hints") or {}).get("data-types"),
            "from": _norm(s.get("effective_from")), "places": set(place_hits(r.text or ""))}


def _p5_similar(a: dict, b: dict) -> bool:
    """P5 part (b) without the timing: similar text, and the same title or the same place, notice type and date span."""
    if not (SequenceMatcher(None, a["title"], b["title"]).ratio() >= 0.75 or SequenceMatcher(None, a["body"], b["body"]).ratio() >= 0.5):
        return False
    if a["title"] and a["title"] == b["title"]:
        return True
    same_place = bool(a["places"] & b["places"]) or (not a["places"] and not b["places"])
    same_span = a["type"] == "live-update" or bool(a["from"] and a["from"] == b["from"])
    return same_place and a["type"] == b["type"] and same_span


class TravelUpdatesAdapter(Adapter):
    source = "travel_updates"
    reliability_hint = "official_operator"

    def accepts(self, meta: dict[str, Any]) -> bool:
        return str(meta.get("tag") or "") == "updates"

    def records(self, body: bytes, meta: dict[str, Any]) -> list[EvidenceRecord]:
        d = load_json(body)
        fetched = self.fetched_at(meta)
        year = int((fetched or "2026")[:4])
        out = []
        for u in d.get("updates") or []:
            title, text_body = str(u.get("title") or "").strip(), str(u.get("body") or "").strip()
            text = f"{title}. {text_body}".strip(". ") if title and text_body else (title or text_body)
            if not text:
                continue
            hits = place_hits(text)
            mode = u.get("mode_guess") or "unknown"
            out.append(EvidenceRecord(
                source=self.source, kind="notice", domain="transport", text=text,
                structured={"title": title, "body": text_body, "mode_guess": mode, "hidden": u.get("hidden"),
                            "effective_from": u.get("effective_from"), "effective_until": u.get("effective_until"),
                            "last_updated": u.get("last_updated"), "hints": u.get("hints")},
                place=Place(name=hits[0] if hits else None, confidence=0.6 if hits else 0.0, method="lexicon" if hits else "none"),
                observed_at=parse_human_datetime(u.get("last_updated"), year) or fetched, fetched_at=fetched,
                valid_from=parse_human_datetime(u.get("effective_from"), year) or fetched,
                valid_to=parse_human_datetime(u.get("effective_until"), year),
                native_id="tu_" + hashlib.sha1((title + "\n" + text_body).encode("utf-8")).hexdigest()[:16],
                reliability_hint=self.reliability_hint, provenance=self.provenance(meta)))
        return out

    def lineage_keys(self, record: EvidenceRecord, opts: Optional[dict] = None) -> list[str]:
        title, body = _norm(record.structured.get("title")), _norm(record.structured.get("body"))
        keys = [f"body:{body}" if body else ""]
        if _rule(opts) == "title_or_body":
            keys.append(f"title:{title}" if title else "")
        return [k for k in keys if k] or [record.record_id]

    def lineage_links(self, records: list[EvidenceRecord], captures: list[tuple[str, str]], opts: Optional[dict] = None):
        if _rule(opts) != "p5":
            return
        window_s = float((opts or {}).get("window_h", 8)) * 3600
        caps = sorted(set(captures) | {(r.fetched_at or "", r.provenance.get("file") or "") for r in records})
        idx = {c: i for i, c in enumerate(caps)}
        when = [datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ") if at else None for at, _ in caps]
        versions: dict[str, dict] = {}                 # native_id -> first/last capture index and one record
        for r in records:
            i = idx[(r.fetched_at or "", r.provenance.get("file") or "")]
            v = versions.setdefault(r.native_id or r.record_id, {"first": i, "last": i, "rec": r})
            v["first"], v["last"] = min(v["first"], i), max(v["last"], i)
        order = sorted(versions.values(), key=lambda v: v["first"])
        feats = {id(v): _features(v["rec"]) for v in order}
        for n, v in enumerate(order):
            i = v["first"]
            for m in order[:n]:
                if m["first"] >= i or m["last"] >= i:       # not earlier, or still on the page: another notice
                    continue
                gone_just_before = m["last"] == i - 1
                if not gone_just_before and not (when[i] and when[m["last"]] and (when[i] - when[m["last"]]).total_seconds() <= window_s):
                    continue
                if _p5_similar(feats[id(m)], feats[id(v)]):
                    yield m["rec"], v["rec"]


register(TravelUpdatesAdapter())

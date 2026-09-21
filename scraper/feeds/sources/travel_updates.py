"""travelnortheast.uk live updates (Metro/bus/ferry) scraped with Playwright: kind=notice, domain=transport.
Only the `updates` JSON payload is adapted (the raw `page` HTML is the reproducible record, not evidence).
The site gives no stable id, so the native id is a hash of title+body; `effective_from/until` are human
strings ("Mon 22 Sep, 9am") parsed best-effort into UTC instants relative to the capture year."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
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


register(TravelUpdatesAdapter())

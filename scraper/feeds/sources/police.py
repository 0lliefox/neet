"""Police.uk street-level crimes: kind=crime_record, month validity, point geometry (anonymised snap point).
`rendered_text` reproduces the v1 sentence so the rendered-vs-feed ablation has both conditions."""
from __future__ import annotations

from typing import Any

from ..base import Adapter, load_json, month_bounds, register
from ..geo import point
from ..model import EvidenceRecord, Place


def render(crime: dict[str, Any]) -> str:
    cat = str(crime.get("category", "")).replace("-", " ").strip()
    street = ((crime.get("location") or {}).get("street") or {}).get("name", "")
    month = crime.get("month", "")
    outcome = ((crime.get("outcome_status") or {}) or {}).get("category")
    s = f"{cat.capitalize()} recorded {street} in {month}."
    if outcome:
        s += f" Outcome: {outcome}."
    return s


class PoliceAdapter(Adapter):
    source = "police"
    reliability_hint = "official_signed"

    def records(self, body: bytes, meta: dict[str, Any]) -> list[EvidenceRecord]:
        crimes = load_json(body)
        if not isinstance(crimes, list):
            return []
        out = []
        for c in crimes:
            loc = c.get("location") or {}
            street = (loc.get("street") or {}).get("name")
            vf, vt = month_bounds(str(c.get("month", "")))
            geom = point(loc.get("longitude"), loc.get("latitude"))
            out.append(EvidenceRecord(
                source=self.source, kind="crime_record", domain="crime", text=None, rendered_text=render(c),
                structured={k: c.get(k) for k in ("category", "location_type", "location_subtype", "context", "outcome_status", "month", "id", "persistent_id")}
                | {"street_name": street, "street_id": (loc.get("street") or {}).get("id")},
                place=Place(name=street, geometry=geom, confidence=0.9 if geom else 0.0, method="source_geometry" if geom else "none"),
                observed_at=vf, fetched_at=self.fetched_at(meta), valid_from=vf, valid_to=vt,
                native_id=str(c.get("persistent_id") or c.get("id") or "") or None,
                reliability_hint=self.reliability_hint, provenance=self.provenance(meta)))
        return out


register(PoliceAdapter())

"""FixMyStreet Open311 service requests: citizen reports with a category, a point and a status.
kind=post (citizen-authored), reliability social; the report text is `title` + `detail`."""
from __future__ import annotations

from typing import Any

from ..base import Adapter, iso_utc, load_json, register
from ..geo import point
from ..model import EvidenceRecord, Place

_CATEGORY_DOMAIN = {
    "flood": "flood", "flooding": "flood", "drain": "flood", "gully": "flood", "blocked drain": "flood",
    "pothole": "roadworks", "road": "roadworks", "pavement": "roadworks", "roadworks": "roadworks", "traffic": "transport",
    "street light": "roadworks", "bus": "transport", "abandoned vehicles": "crime", "fly": "other", "flytipping": "other",
}


def domain_for(service_name: str) -> str:
    s = (service_name or "").lower()
    for key, dom in _CATEGORY_DOMAIN.items():
        if key in s:
            return dom
    return "other"


class FixMyStreetAdapter(Adapter):
    source = "fixmystreet"
    reliability_hint = "social"

    def records(self, body: bytes, meta: dict[str, Any]) -> list[EvidenceRecord]:
        d = load_json(body)
        out = []
        for r in (d.get("service_requests") or []):
            title, detail = str(r.get("title") or "").strip(), str(r.get("detail") or r.get("description") or "").strip()
            text = title if not detail or detail == title else (f"{title}. {detail}" if title else detail)
            if not text:
                continue
            geom = point(r.get("long"), r.get("lat"))
            t = iso_utc(r.get("requested_datetime"))
            agency = r.get("agency_responsible")
            recipients = agency.get("recipient") if isinstance(agency, dict) else agency
            out.append(EvidenceRecord(
                source=self.source, kind="post", domain=domain_for(r.get("service_name")), text=text,
                structured={k: r.get(k) for k in ("service_request_id", "service_code", "service_name", "status", "requested_datetime",
                                                   "updated_datetime", "interface_used")} | {"agency_responsible": recipients, "body": meta.get("body")},
                place=Place(name=None, geometry=geom, confidence=0.9 if geom else 0.0, method="source_geometry" if geom else "none"),
                observed_at=t, fetched_at=self.fetched_at(meta), valid_from=t, valid_to=iso_utc(r.get("updated_datetime")) if str(r.get("status")) in ("closed", "fixed") else None,
                native_id=str(r.get("service_request_id") or "") or None, reliability_hint=self.reliability_hint, provenance=self.provenance(meta)))
        return out


register(FixMyStreetAdapter())

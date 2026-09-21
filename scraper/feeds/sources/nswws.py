"""Met Office NSWWS warning objects (GeoJSON): kind=warning, domain=weather, polygon geometry.
The field names of the CAP-style properties are taken from the NSWWS v1.1 documentation and are marked as
unverified until a live warning is captured (WS1 assumption A4): the adapter reads several spellings."""
from __future__ import annotations

from typing import Any

from ..base import Adapter, first, iso_utc, load_json, register
from ..model import EvidenceRecord, Place


class NswwsAdapter(Adapter):
    source = "nswws"
    reliability_hint = "official_signed"

    def accepts(self, meta: dict[str, Any]) -> bool:
        return str(meta.get("tag") or "") != "feed"   # the Atom index is not evidence

    def records(self, body: bytes, meta: dict[str, Any]) -> list[EvidenceRecord]:
        d = load_json(body)
        feats = d.get("features") if isinstance(d, dict) else None
        if not isinstance(feats, list):
            return []
        out = []
        for f in feats:
            p = f.get("properties") or {}
            headline = first(p, "headline", "Headline", "title", default=None)
            text = " ".join(x for x in (headline, first(p, "description", "Description", "warningText", "text", default=None),
                                        first(p, "instruction", "whatToExpect", default=None)) if x) or None
            level = first(p, "warningLevel", "level", "severity", "colour", default=None)
            wtype = first(p, "weatherType", "warningType", "type", default=None)
            regions = first(p, "regions", "areas", "affectedAreas", default=None)
            vf = iso_utc(first(p, "validFrom", "onset", "startTime", "effective", default=None))
            vt = iso_utc(first(p, "validTo", "expires", "endTime", default=None))
            issued = iso_utc(first(p, "issuedTime", "issued", "sent", "modifiedTime", default=None))
            wid = first(p, "warningId", "id", "identifier", default=None) or str(meta.get("tag") or "").split("_", 1)[-1]
            geom = f.get("geometry") if isinstance(f.get("geometry"), dict) else None
            region_name = None
            if isinstance(regions, list) and regions:
                region_name = str(regions[0]) if not isinstance(regions[0], dict) else first(regions[0], "name", "regionName", default=None)
            out.append(EvidenceRecord(
                source=self.source, kind="warning", domain="weather", text=text,
                structured={"warningLevel": level, "weatherType": wtype, "regions": regions, "warningId": wid, "status": first(p, "status", "msgType", default=None),
                            "raw_property_keys": sorted(p.keys())},
                place=Place(name=region_name, geometry=geom, confidence=1.0 if geom else 0.3, method="source_geometry" if geom else "none"),
                observed_at=issued, fetched_at=self.fetched_at(meta), valid_from=vf, valid_to=vt,
                native_id=str(wid) if wid else None, reliability_hint=self.reliability_hint, provenance=self.provenance(meta)))
        return out


register(NswwsAdapter())

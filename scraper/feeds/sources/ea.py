"""Environment Agency flood-monitoring: flood warnings (kind=warning, text = the public message), station
inventories (kept as structured place records for joins) and 15-min readings (kind=observation)."""
from __future__ import annotations

from typing import Any

from ..base import Adapter, iso_utc, load_json, register
from ..geo import point
from ..model import EvidenceRecord, Place

_SEVERITY = {1: "Severe Flood Warning", 2: "Flood Warning", 3: "Flood Alert", 4: "Warning no longer in force"}


class EaAdapter(Adapter):
    source = "ea"
    reliability_hint = "official_signed"

    def records(self, body: bytes, meta: dict[str, Any]) -> list[EvidenceRecord]:
        tag = str(meta.get("tag") or "")
        d = load_json(body)
        items = d.get("items") if isinstance(d, dict) else None
        if not isinstance(items, list):
            return []
        if tag.startswith("floods_"):
            return [self._flood(i, meta) for i in items]
        if tag.startswith("stations_"):
            return [self._station(i, meta, tag) for i in items]
        if tag.startswith("readings_"):
            return [self._reading(i, meta) for i in items]
        return []

    def _flood(self, i: dict, meta: dict) -> EvidenceRecord:
        area = i.get("floodArea") or {}
        sev = i.get("severityLevel")
        text = " ".join(x for x in (i.get("description"), i.get("message")) if x) or None
        return EvidenceRecord(
            source=self.source, kind="warning", domain="flood", text=text,
            structured={"severityLevel": sev, "severity": i.get("severity") or _SEVERITY.get(sev), "floodAreaID": i.get("floodAreaID"),
                        "eaAreaName": i.get("eaAreaName"), "county": area.get("county"), "riverOrSea": area.get("riverOrSea"),
                        "polygon": area.get("polygon"), "isTidal": i.get("isTidal")},
            place=Place(name=area.get("county") or i.get("eaAreaName"), admin_area=area.get("county"), confidence=0.5, method="source_geometry" if area.get("polygon") else "none"),
            observed_at=iso_utc(i.get("timeMessageChanged") or i.get("timeRaised")), fetched_at=self.fetched_at(meta),
            valid_from=iso_utc(i.get("timeRaised")), valid_to=None, native_id=str(i.get("floodAreaID") or "") or None,
            reliability_hint=self.reliability_hint, provenance=self.provenance(meta))

    def _station(self, i: dict, meta: dict, tag: str) -> EvidenceRecord:
        geom = point(i.get("long"), i.get("lat"))
        return EvidenceRecord(
            source=self.source, kind="observation", domain="flood", text=None,
            structured={"station": True, "parameter": tag.replace("stations_", ""), "stationReference": i.get("stationReference"),
                        "label": i.get("label"), "riverName": i.get("riverName"), "town": i.get("town"), "notation": i.get("notation"),
                        "measures": [m.get("@id") if isinstance(m, dict) else m for m in (i.get("measures") or [])]},
            place=Place(name=i.get("label"), geometry=geom, confidence=1.0 if geom else 0.0, method="source_geometry" if geom else "none"),
            observed_at=None, fetched_at=self.fetched_at(meta), native_id=str(i.get("stationReference") or i.get("@id") or "") or None,
            reliability_hint=self.reliability_hint, provenance=self.provenance(meta))

    def _reading(self, i: dict, meta: dict) -> EvidenceRecord:
        t = iso_utc(i.get("dateTime"))
        measure = str(i.get("measure") or "")
        return EvidenceRecord(
            source=self.source, kind="observation", domain="flood", text=None,
            structured={"measure": measure, "value": i.get("value"), "station": (meta.get("station") or measure.split("/measures/")[-1].split("-")[0]),
                        "parameter": meta.get("parameter") or ("rainfall" if "rainfall" in measure else "level")},
            place=Place(name=None, confidence=0.0, method="none"),   # joined to the station inventory by `station`
            observed_at=t, fetched_at=self.fetched_at(meta), valid_from=t, valid_to=t,
            native_id=str(i.get("@id") or "") or None, reliability_hint=self.reliability_hint, provenance=self.provenance(meta))


register(EaAdapter())

"""Urban Observatory v2: the sensor inventory (place records for joins) and per-variable data pages
(kind=observation, one record per reading, with the sensor's centroid as geometry when the page carries it).
Readings are telemetry: no text; civic-verifier's grounding turns windows of them into atoms (WS5)."""
from __future__ import annotations

from typing import Any

from ..base import Adapter, iso_utc, load_json, register
from ..geo import point, wkt_to_geojson
from ..model import EvidenceRecord, Place

_VARIABLE_DOMAIN = {
    "Journey Time": "transport", "Traffic Flow": "transport", "Congestion": "transport", "Average Speed": "transport",
    "NO2": "weather", "Temperature": "weather", "Humidity": "weather", "Wind Speed": "weather", "Wind Direction": "weather",
    "Rainfall": "flood", "Pressure": "weather", "Solar Radiation": "weather", "Occupied spaces": "transport",
}


def _sensor_place(s: dict[str, Any]) -> Place:
    geom = None
    if s.get("Location_WKT"):
        geom = wkt_to_geojson(str(s["Location_WKT"]))
    if geom is None:
        geom = point(s.get("Sensor_Centroid_Longitude"), s.get("Sensor_Centroid_Latitude"))
    return Place(name=s.get("Sensor_Name"), geometry=geom, confidence=1.0 if geom else 0.0, method="source_geometry" if geom else "none")


class UoAdapter(Adapter):
    source = "uo"
    reliability_hint = "official_signed"

    def records(self, body: bytes, meta: dict[str, Any]) -> list[EvidenceRecord]:
        d = load_json(body)
        tag = str(meta.get("tag") or "")
        if tag.startswith("sensors_inventory"):
            return [self._sensor(s, meta) for s in (d.get("Sensors") or []) if isinstance(s, dict)]
        sensors = d.get("Sensors") or {}
        out = []
        for r in d.get("Readings") or []:
            name = r.get("Sensor_Name")
            var = str(r.get("Variable") or meta.get("variable") or "")
            t = iso_utc(r.get("Timestamp"))
            sinfo = sensors.get(name) if isinstance(sensors, dict) else None
            place = _sensor_place(sinfo) if isinstance(sinfo, dict) else Place(name=name, confidence=0.0, method="none")
            out.append(EvidenceRecord(
                source=self.source, kind="observation", domain=_VARIABLE_DOMAIN.get(var, "other"), text=None,
                structured={"Sensor_Name": name, "Variable": var, "Value": r.get("Value"), "Flagged": r.get("Flagged"),
                            "Units": (sinfo or {}).get("Units") if isinstance(sinfo, dict) else None},
                place=place, observed_at=t, fetched_at=self.fetched_at(meta), valid_from=t, valid_to=t,
                native_id=f"uo_{name}_{var}_{r.get('Timestamp')}", reliability_hint=self.reliability_hint, provenance=self.provenance(meta)))
        return out

    def _sensor(self, s: dict[str, Any], meta: dict[str, Any]) -> EvidenceRecord:
        return EvidenceRecord(
            source=self.source, kind="observation", domain="other", text=None,
            structured={"sensor": True, **{k: s.get(k) for k in ("Sensor_Name", "Broker_Name", "Raw_ID", "Third_Party", "Sensor_Height_Above_Ground",
                                                                  "Ground_Height_Above_Sea_Level", "Location_WKT")}},
            place=_sensor_place(s), observed_at=None, fetched_at=self.fetched_at(meta),
            native_id=f"uo_sensor_{s.get('Sensor_Name')}", reliability_hint=self.reliability_hint, provenance=self.provenance(meta))


register(UoAdapter())

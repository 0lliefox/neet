"""one.network tile properties: the only source of the promoter-authored telegraphic `works_desc`.
kind=notice, domain=roadworks, validity = start_date/end_date, place = usrn + road name (tile geometry is
kept when the capture stores it — see `RoadworksScraper._iter_tile_features`; older captures have none)."""
from __future__ import annotations

from typing import Any

from ..base import Adapter, first, iso_utc, load_json, register
from ..geo import wkt_to_geojson
from ..model import EvidenceRecord, Place

def _impact_labels() -> dict[str, str]:
    from scraper.scrapers.roadworks import RoadworksScraper   # single source of the one.network impact vocabulary
    return RoadworksScraper.IMPACT_LABELS


def render(p: dict[str, Any]) -> str:
    road = first(p, "road_name", "usrn_street", "address", default="")
    desc = first(p, "works_desc", default="")
    tm = first(p, "traffman", default="")
    delay = first(p, "delay", default=None)
    impact = p.get("impact")
    parts = [f"Roadworks on {road}: {desc}".strip(": ")]
    if tm:
        parts.append(f"Traffic management: {tm}.")
    if delay:
        parts.append(f"{delay}.")
    elif impact is not None and str(impact) in _impact_labels():
        parts.append(f"{_impact_labels()[str(impact)].capitalize()}.")
    end = first(p, "end_date", default=None)
    if end:
        parts.append(f"Work expected to end {str(end)[:10]}.")
    return " ".join(x for x in parts if x)


class OneNetworkAdapter(Adapter):
    source = "onenetwork"
    reliability_hint = "official_operator"

    def records(self, body: bytes, meta: dict[str, Any]) -> list[EvidenceRecord]:
        d = load_json(body)
        feats = d.get("features") if isinstance(d, dict) else d
        out = []
        for f in feats or []:
            p = f.get("properties", f) if isinstance(f, dict) else {}
            geom = p.get("_geometry") if isinstance(p.get("_geometry"), dict) else None   # kept by the tile scraper (WS1)
            if geom is None and isinstance(f, dict) and isinstance(f.get("geometry"), dict):
                geom = f["geometry"]
            if geom is None and isinstance(p.get("geometry"), str):
                geom = wkt_to_geojson(p["geometry"])
            desc = first(p, "works_desc", default=None)
            road = first(p, "road_name", "usrn_street", default=None)
            vf, vt = iso_utc(first(p, "start_date", "startdate")), iso_utc(first(p, "end_date", "enddate"))
            out.append(EvidenceRecord(
                source=self.source, kind="notice", domain="roadworks", text=desc, rendered_text=render(p),
                structured={k: p.get(k) for k in ("id", "work_ref", "usrn", "road_name", "pub_name", "resporg_name", "swtype", "traffman", "delay",
                                                   "impact", "works_state", "permit_status", "ttro_state", "start_date", "end_date", "_tile", "_layer")},
                place=Place(name=road, usrn=str(p["usrn"]) if p.get("usrn") not in (None, "") else None, geometry=geom,
                            confidence=1.0 if geom else (0.8 if p.get("usrn") else 0.0), method="source_geometry" if geom else ("usrn" if p.get("usrn") else "none")),
                observed_at=self.fetched_at(meta), fetched_at=self.fetched_at(meta), valid_from=vf, valid_to=vt,
                native_id=str(first(p, "work_ref", "id", default="")) or None, reliability_hint=self.reliability_hint, provenance=self.provenance(meta)))
        return out


register(OneNetworkAdapter())

"""Street Manager open-data monthly archives (zip of CSVs, permits / activities / section 58): kind=notice,
domain=roadworks, USRN + WKT geometry in British National Grid (converted to WGS84), proposed/actual date
pairs as validity. Archive rows carry no promoter free text (WS1 assumption A3): `text` is None and
`rendered_text` is synthesised from the structured fields; the one.network `works_desc` is joined later by
`work_reference_number`. Column names follow the published schema and are read case-insensitively."""
from __future__ import annotations

import csv
import io
import zipfile
from typing import Any, Optional

from ..base import Adapter, iso_utc, register
from ..geo import wkt_to_geojson
from ..model import EvidenceRecord, Place

_COLS = ("permit_reference_number", "work_reference_number", "usrn", "street_name", "area_name", "town", "highway_authority",
         "promoter_organisation", "work_category", "activity_type", "traffic_management_type", "work_status", "permit_status",
         "is_traffic_sensitive", "close_footway", "proposed_start_date", "proposed_end_date", "actual_start_date_time",
         "actual_end_date_time", "event_type", "event_time", "description_of_work", "collaborative_working", "road_category")


def render(r: dict[str, Any]) -> str:
    street = r.get("street_name") or "an unnamed street"
    town = r.get("town") or r.get("area_name") or ""
    act = (r.get("activity_type") or r.get("work_category") or "roadworks").replace("_", " ")
    tm = (r.get("traffic_management_type") or "").replace("_", " ")
    s = f"Roadworks on {street}{', ' + town if town else ''}: {act}."
    if tm:
        s += f" Traffic management: {tm}."
    end = r.get("proposed_end_date") or r.get("actual_end_date_time")
    if end:
        s += f" Work expected to end {str(end)[:10]}."
    return s


class StreetManagerArchiveAdapter(Adapter):
    source = "streetmanager_archive"
    reliability_hint = "official_signed"

    def records(self, body: bytes, meta: dict[str, Any]) -> list[EvidenceRecord]:
        out: list[EvidenceRecord] = []
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".csv"):
                    continue
                with zf.open(name) as fh:
                    reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"))
                    for row in reader:
                        r = {str(k).strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k is not None}
                        out.append(self._record(r, meta, name))
        return out

    def _record(self, r: dict[str, Any], meta: dict[str, Any], member: str) -> EvidenceRecord:
        geom = wkt_to_geojson(r.get("works_location_coordinates") or r.get("geometry") or "", crs_bng=True)
        vf = iso_utc(r.get("actual_start_date_time") or r.get("proposed_start_date"))
        vt = iso_utc(r.get("actual_end_date_time") or r.get("proposed_end_date"))
        text: Optional[str] = r.get("description_of_work") or None
        return EvidenceRecord(
            source=self.source, kind="notice", domain="roadworks", text=text, rendered_text=render(r),
            structured={k: r.get(k) for k in _COLS if k in r} | {"archive_member": member},
            place=Place(name=r.get("street_name"), usrn=r.get("usrn") or None, geometry=geom, admin_area=r.get("town") or r.get("area_name"),
                        confidence=1.0 if geom else (0.8 if r.get("usrn") else 0.0), method="source_geometry" if geom else ("usrn" if r.get("usrn") else "none")),
            observed_at=iso_utc(r.get("event_time")) or self.fetched_at(meta), fetched_at=self.fetched_at(meta), valid_from=vf, valid_to=vt,
            native_id=r.get("permit_reference_number") or r.get("work_reference_number") or None,
            reliability_hint=self.reliability_hint, provenance=self.provenance(meta))


register(StreetManagerArchiveAdapter())

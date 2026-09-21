"""Sensor windows over observation records (WS1 §6): the readings of one variable near a place in a time window.

Only selection and distance happen here; turning a window into a grounding atom (bands, baselines) is WS5.

    from scraper.feeds.windows import window
    w = window(records, variable="Journey Time", lon=-1.61, lat=54.97, radius_m=1500,
               t0="2026-09-19T07:00:00Z", t1="2026-09-19T10:00:00Z")
    w.readings -> [(timestamp, value, flagged, sensor_name, distance_m), ...] sorted by time
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .model import EvidenceRecord


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def geometry_centroid(geom: Optional[dict]) -> Optional[tuple[float, float]]:
    if not geom or "coordinates" not in geom:
        return None
    pts: list = []

    def walk(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            pts.append((float(c[0]), float(c[1])))
        elif isinstance(c, (list, tuple)):
            for x in c:
                walk(x)
    walk(geom["coordinates"])
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


@dataclass
class Window:
    variable: str
    lon: float
    lat: float
    radius_m: float
    t0: Optional[str]
    t1: Optional[str]
    readings: list[tuple] = field(default_factory=list)   # (timestamp, value, flagged, sensor_name, distance_m)
    sensors: dict[str, float] = field(default_factory=dict)  # sensor_name -> distance_m

    @property
    def values(self) -> list[float]:
        out = []
        for _, v, *_ in self.readings:
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                pass
        return out

    @property
    def unflagged_values(self) -> list[float]:
        out = []
        for _, v, flagged, *_ in self.readings:
            if flagged:
                continue
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                pass
        return out

    def summary(self) -> dict:
        vals = self.unflagged_values
        return {"variable": self.variable, "n": len(self.readings), "n_unflagged": len(vals), "sensors": len(self.sensors),
                "min": min(vals) if vals else None, "max": max(vals) if vals else None,
                "mean": (sum(vals) / len(vals)) if vals else None, "t0": self.t0, "t1": self.t1, "radius_m": self.radius_m}


def _variable_of(r: EvidenceRecord) -> Optional[str]:
    s = r.structured
    if r.source == "uo":
        return s.get("Variable")
    if r.source == "ea" and "value" in s:
        return {"rainfall": "Rainfall", "level": "River Level"}.get(str(s.get("parameter") or ""), s.get("parameter"))
    if r.source == "metoffice_hourly":
        return "Forecast"
    return None


def window(records: Iterable[EvidenceRecord], variable: str, lon: float, lat: float, radius_m: float,
           t0: Optional[str] = None, t1: Optional[str] = None, include_flagged: bool = True) -> Window:
    w = Window(variable, lon, lat, radius_m, t0, t1)
    for r in records:
        if r.kind != "observation" or _variable_of(r) != variable:
            continue
        t = r.observed_at or r.valid_from
        if not t or (t0 and t < t0) or (t1 and t > t1):
            continue
        c = geometry_centroid(r.place.geometry)
        if c is None:
            continue
        d = haversine_m(lon, lat, c[0], c[1])
        if d > radius_m:
            continue
        flagged = bool(r.structured.get("Flagged")) if r.source == "uo" else False
        if flagged and not include_flagged:
            continue
        name = str(r.structured.get("Sensor_Name") or r.structured.get("station") or r.place.name or "")
        w.readings.append((t, r.structured.get("Value", r.structured.get("value")), flagged, name, round(d)))
        w.sensors[name] = min(w.sensors.get(name, d), d)
    w.readings.sort()
    return w

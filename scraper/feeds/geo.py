"""Geometry helpers for evidence records: WKT -> GeoJSON, British National Grid -> WGS84, lexicon place hits.

The OSGB36 -> WGS84 transformation is the standard Helmert + inverse transverse Mercator (Ordnance Survey,
"A guide to coordinate systems in Great Britain"); accuracy ~5 m, which is far below the spatial resolution
any civic claim needs. pyproj is used instead when installed.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Optional

try:  # optional, more accurate
    from pyproj import Transformer  # type: ignore
    _BNG_TO_WGS84 = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
except Exception:  # pragma: no cover
    _BNG_TO_WGS84 = None


def bng_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """(E, N) in EPSG:27700 -> (lon, lat) in EPSG:4326."""
    if _BNG_TO_WGS84 is not None:
        lon, lat = _BNG_TO_WGS84.transform(easting, northing)
        return float(lon), float(lat)
    # --- inverse transverse Mercator on the Airy 1830 ellipsoid (OSGB36) ---
    a, b = 6377563.396, 6356256.909
    F0, lat0, lon0 = 0.9996012717, math.radians(49.0), math.radians(-2.0)
    N0, E0 = -100000.0, 400000.0
    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)
    lat = lat0
    M = 0.0
    while True:
        lat = (northing - N0 - M) / (a * F0) + lat
        Ma = (1 + n + 1.25 * n ** 2 + 1.25 * n ** 3) * (lat - lat0)
        Mb = (3 * n + 3 * n ** 2 + 2.625 * n ** 3) * math.sin(lat - lat0) * math.cos(lat + lat0)
        Mc = (1.875 * n ** 2 + 1.875 * n ** 3) * math.sin(2 * (lat - lat0)) * math.cos(2 * (lat + lat0))
        Md = (35 / 24) * n ** 3 * math.sin(3 * (lat - lat0)) * math.cos(3 * (lat + lat0))
        M = b * F0 * (Ma - Mb + Mc - Md)
        if abs(northing - N0 - M) < 1e-5:
            break
    sin_lat, cos_lat, tan_lat = math.sin(lat), math.cos(lat), math.tan(lat)
    nu = a * F0 / math.sqrt(1 - e2 * sin_lat ** 2)
    rho = a * F0 * (1 - e2) * (1 - e2 * sin_lat ** 2) ** -1.5
    eta2 = nu / rho - 1
    VII = tan_lat / (2 * rho * nu)
    VIII = tan_lat / (24 * rho * nu ** 3) * (5 + 3 * tan_lat ** 2 + eta2 - 9 * tan_lat ** 2 * eta2)
    IX = tan_lat / (720 * rho * nu ** 5) * (61 + 90 * tan_lat ** 2 + 45 * tan_lat ** 4)
    X = 1 / (cos_lat * nu)
    XI = (nu / rho + 2 * tan_lat ** 2) / (6 * cos_lat * nu ** 3)
    XII = (5 + 28 * tan_lat ** 2 + 24 * tan_lat ** 4) / (120 * cos_lat * nu ** 5)
    XIIA = (61 + 662 * tan_lat ** 2 + 1320 * tan_lat ** 4 + 720 * tan_lat ** 6) / (5040 * cos_lat * nu ** 7)
    dE = easting - E0
    lat_os = lat - VII * dE ** 2 + VIII * dE ** 4 - IX * dE ** 6
    lon_os = lon0 + X * dE - XI * dE ** 3 + XII * dE ** 5 - XIIA * dE ** 7
    # --- Helmert OSGB36 -> WGS84 ---
    H = 0.0
    sin_p, cos_p, sin_l, cos_l = math.sin(lat_os), math.cos(lat_os), math.sin(lon_os), math.cos(lon_os)
    nu1 = a / math.sqrt(1 - e2 * sin_p ** 2)
    x1 = (nu1 + H) * cos_p * cos_l
    y1 = (nu1 + H) * cos_p * sin_l
    z1 = ((1 - e2) * nu1 + H) * sin_p
    tx, ty, tz = 446.448, -125.157, 542.060
    rx, ry, rz = (math.radians(x / 3600) for x in (0.1502, 0.2470, 0.8421))
    s = 1 + (-20.4894 / 1e6)
    x2 = tx + s * x1 - rz * y1 + ry * z1
    y2 = ty + rz * x1 + s * y1 - rx * z1
    z2 = tz - ry * x1 + rx * y1 + s * z1
    a2, b2 = 6378137.000, 6356752.3142
    e2b = 1 - (b2 * b2) / (a2 * a2)
    p = math.sqrt(x2 * x2 + y2 * y2)
    phi = math.atan2(z2, p * (1 - e2b))
    for _ in range(10):
        nu2 = a2 / math.sqrt(1 - e2b * math.sin(phi) ** 2)
        phi = math.atan2(z2 + e2b * nu2 * math.sin(phi), p)
    lam = math.atan2(y2, x2)
    return math.degrees(lam), math.degrees(phi)


_WKT_NUM = r"-?\d+(?:\.\d+)?"


def wkt_to_geojson(wkt: str, crs_bng: bool = False) -> Optional[dict]:
    """POINT / LINESTRING / POLYGON WKT -> GeoJSON (optionally converting BNG coordinates to WGS84)."""
    if not wkt or not isinstance(wkt, str):
        return None
    m = re.match(r"\s*(POINT|LINESTRING|POLYGON)\s*\((.*)\)\s*$", wkt.strip(), re.I | re.S)
    if not m:
        return None
    kind, body = m.group(1).upper(), m.group(2)

    def coords(text: str) -> list[list[float]]:
        out = []
        for pair in text.split(","):
            nums = re.findall(_WKT_NUM, pair)
            if len(nums) >= 2:
                x, y = float(nums[0]), float(nums[1])
                if crs_bng:
                    x, y = bng_to_wgs84(x, y)
                out.append([round(x, 6), round(y, 6)])
        return out

    if kind == "POINT":
        c = coords(body)
        return {"type": "Point", "coordinates": c[0]} if c else None
    if kind == "LINESTRING":
        return {"type": "LineString", "coordinates": coords(body)}
    rings = [coords(r) for r in re.findall(r"\(([^()]*)\)", body)]
    return {"type": "Polygon", "coordinates": rings}


def point(lon: Any, lat: Any) -> Optional[dict]:
    try:
        return {"type": "Point", "coordinates": [round(float(lon), 6), round(float(lat), 6)]}
    except (TypeError, ValueError):
        return None


# -- lexicon place hits (name only; gazetteer coordinates are a later WS1 step) --------------------
_LEXICON: Optional[dict] = None


def lexicon() -> dict:
    global _LEXICON
    if _LEXICON is None:
        import os
        import yaml
        path = os.path.join(os.path.dirname(__file__), "..", "capture", "lexicon", "newcastle.yaml")
        try:
            with open(path, encoding="utf-8") as f:
                _LEXICON = yaml.safe_load(f) or {}
        except OSError:
            _LEXICON = {}
    return _LEXICON


def place_hits(text: str, tiers: Iterable[str] = ("tier1", "tier2_metro_stations")) -> list[str]:
    """Place names from the shared gazetteer that occur in `text` (longest first, case-insensitive)."""
    if not text:
        return []
    places = lexicon().get("places", {}) or {}
    names: list[str] = []
    for tier in tiers:
        names.extend(str(n) for n in (places.get(tier) or []))
    low = text.lower()
    hits = [n for n in sorted(set(names), key=len, reverse=True) if re.search(r"\b" + re.escape(n.lower()) + r"\b", low)]
    return hits

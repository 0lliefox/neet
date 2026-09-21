"""Gazetteer for evidence records: named places and roads of the study area with WGS84 geometry.

Built once from two open datasets and committed as a derived file (`scraper/feeds/data/gazetteer.json.gz`, ~1.5 MB):
* GeoNames GB dump (CC BY 4.0) — settlements, stations, hospitals, parks… inside the study bbox;
* OS Open Names (OGL v3) — named roads and populated places, from the 100 km tiles covering the bbox.

    python -m scraper.feeds.gazetteer build --geonames GB.txt --opennames opnames.zip --out scraper/feeds/data/gazetteer.json.gz

Lookups are exact, case-insensitive, over names and alternate names; a name that appears more than once in the
area (there are two "High Street"s) resolves to the entry with the largest population/importance and is marked
`ambiguous` so the record's place confidence is lowered. No live geocoding service is called (reproducibility).
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import zipfile
from typing import Any, Iterable, Optional

BBOX = {"min_lon": -1.95, "max_lon": -1.30, "min_lat": 54.85, "max_lat": 55.10}   # Tyne and Wear + fringes
DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "gazetteer.json.gz")
_OPENNAMES_TILES = ("NZ1", "NZ2", "NZ3")   # 100 km square NZ, eastings 10–39 km cover the bbox

_ROAD_SUFFIX = re.compile(r"\b([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){0,3}\s+(?:Road|Street|Lane|Drive|Avenue|Way|Crescent|Terrace|"
                          r"Place|Gardens|Grove|Close|Bank|Bridge|Bypass|Row|Square|Park|Hill|Walk|View|Court|Quay|Quayside|Motorway))\b")
_A_ROAD = re.compile(r"\b(A\d{1,4}(?:\([MT]\))?|M\d{1,3})\b")


def _in_bbox(lon: float, lat: float) -> bool:
    return BBOX["min_lon"] <= lon <= BBOX["max_lon"] and BBOX["min_lat"] <= lat <= BBOX["max_lat"]


def _geonames_entries(path: str) -> Iterable[dict]:
    with open(path, encoding="utf-8") as f:
        for r in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(r) < 15:
                continue
            lat, lon = float(r[4]), float(r[5])
            if not _in_bbox(lon, lat):
                continue
            yield {"name": r[1], "alt": [a for a in (r[3].split(",") if r[3] else []) if a],
                   "lon": round(lon, 6), "lat": round(lat, 6), "type": f"geonames:{r[6]}.{r[7]}", "importance": int(r[14] or 0),
                   "source": "geonames"}


def _opennames_entries(zip_path: str) -> Iterable[dict]:
    """OS Open Names CSV tiles: columns per the product header file (Doc/OS_Open_Names_Header.csv)."""
    from .geo import bng_to_wgs84
    with zipfile.ZipFile(zip_path) as zf:
        header = None
        for n in zf.namelist():
            if n.lower().endswith("header.csv"):
                header = next(csv.reader(io.TextIOWrapper(zf.open(n), encoding="utf-8")))
                break
        if header is None:
            raise ValueError("OS Open Names zip: header file not found")
        col = {h: i for i, h in enumerate(header)}
        for n in zf.namelist():
            base = os.path.basename(n)
            if not base.lower().endswith(".csv") or not base.upper().startswith(_OPENNAMES_TILES):
                continue
            for r in csv.reader(io.TextIOWrapper(zf.open(n), encoding="utf-8")):
                try:
                    x, y = float(r[col["GEOMETRY_X"]]), float(r[col["GEOMETRY_Y"]])
                except (KeyError, ValueError, IndexError):
                    continue
                lon, lat = bng_to_wgs84(x, y)
                if not _in_bbox(lon, lat):
                    continue
                name = r[col["NAME1"]]
                ltype, ltype2 = r[col["TYPE"]], r[col["LOCAL_TYPE"]]
                yield {"name": name, "alt": [r[col["NAME2"]]] if r[col.get("NAME2", -1)] else [],
                       "lon": round(lon, 6), "lat": round(lat, 6), "type": f"opennames:{ltype}.{ltype2}", "importance": 0,
                       "source": "opennames", "district": r[col.get("DISTRICT_BOROUGH", -1)] if "DISTRICT_BOROUGH" in col else None,
                       "populated_place": r[col.get("POPULATED_PLACE", -1)] if "POPULATED_PLACE" in col else None}


def build(geonames: Optional[str], opennames: Optional[str], out: str) -> dict[str, Any]:
    entries: list[dict] = []
    if geonames:
        entries.extend(_geonames_entries(geonames))
    if opennames:
        entries.extend(_opennames_entries(opennames))
    index: dict[str, list[int]] = {}
    for i, e in enumerate(entries):
        for nm in {e["name"], *e.get("alt", [])}:
            if nm:
                index.setdefault(nm.strip().lower(), []).append(i)
    gaz = {"bbox": BBOX, "entries": entries, "index": index,
           "provenance": {"geonames": bool(geonames), "opennames": bool(opennames)}}
    os.makedirs(os.path.dirname(out), exist_ok=True)
    opener = gzip.open if out.endswith(".gz") else open
    with opener(out, "wt", encoding="utf-8") as f:
        json.dump(gaz, f, ensure_ascii=False, separators=(",", ":"))
    return gaz


_GAZ: Optional[dict] = None


def load(path: str = DATA_PATH) -> dict:
    global _GAZ
    if _GAZ is None:
        try:
            opener = gzip.open if path.endswith(".gz") else open
            with opener(path, "rt", encoding="utf-8") as f:
                _GAZ = json.load(f)
        except OSError:
            _GAZ = {"entries": [], "index": {}}
    return _GAZ


def lookup(name: str, prefer: Iterable[str] = ()) -> Optional[dict]:
    """Best entry for `name`: {'lon','lat','type','ambiguous'} or None. `prefer` = type prefixes tried first."""
    if not name:
        return None
    gaz = load()
    ids = gaz["index"].get(name.strip().lower())
    if not ids:
        return None
    cands = [gaz["entries"][i] for i in ids]
    for p in prefer:
        pc = [c for c in cands if c["type"].startswith(p)]
        if pc:
            cands = pc
            break
    best = max(cands, key=lambda c: (c.get("importance", 0), c["source"] == "opennames"))
    distinct = {(round(c["lon"], 2), round(c["lat"], 2)) for c in cands}
    return {"lon": best["lon"], "lat": best["lat"], "type": best["type"], "ambiguous": len(distinct) > 1, "name": best["name"]}


def road_names(text: str) -> list[str]:
    """Candidate road names in free text ('Malvern Road', 'A1058', 'Central Motorway')."""
    if not text:
        return []
    out = [m.group(1) for m in _ROAD_SUFFIX.finditer(text)]
    out += [m.group(1) for m in _A_ROAD.finditer(text)]
    seen: list[str] = []
    for n in out:
        if n not in seen:
            seen.append(n)
    return seen


def geocode_text(text: str, lexicon_hits: Iterable[str] = ()) -> Optional[dict]:
    """Geometry for a record from its text: a road name first (most specific), then a gazetteer/lexicon place.
    Returns {'name','geometry','confidence','method'} or None."""
    for road in road_names(text):
        hit = lookup(road, prefer=("opennames:transportNetwork", "opennames:"))
        if hit:
            return {"name": road, "geometry": {"type": "Point", "coordinates": [hit["lon"], hit["lat"]]},
                    "confidence": 0.5 if hit["ambiguous"] else 0.8, "method": "gazetteer"}
    for place in lexicon_hits:
        hit = lookup(place, prefer=("geonames:P", "opennames:populatedPlace", "geonames:S.MTRO", "geonames:"))
        if hit:
            return {"name": place, "geometry": {"type": "Point", "coordinates": [hit["lon"], hit["lat"]]},
                    "confidence": 0.4 if hit["ambiguous"] else 0.6, "method": "gazetteer"}
    return None


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="neet gazetteer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--geonames")
    b.add_argument("--opennames")
    b.add_argument("--out", default=DATA_PATH)
    q = sub.add_parser("lookup")
    q.add_argument("name")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        gaz = build(a.geonames, a.opennames, a.out)
        print(f"{len(gaz['entries'])} entries, {len(gaz['index'])} names -> {a.out}")
    else:
        print(json.dumps(lookup(a.name), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

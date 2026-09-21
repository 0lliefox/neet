"""Golden tests for the feed adapters on real captured payloads (tests/fixtures/capture, trimmed slices).

Every adapter must: produce records only of declared kinds/domains, derive validity per the WS1 brief, keep
geometry in WGS84 GeoJSON, and (Bluesky) never let a raw author DID/handle through."""
from __future__ import annotations

import glob
import json
import os
import tempfile
import unittest
from pathlib import Path

from scraper.feeds import adapters
from scraper.feeds.base import iso_utc, month_bounds
from scraper.feeds.build import build_records, report, write_jsonl
from scraper.feeds.geo import bng_to_wgs84, place_hits, wkt_to_geojson
from scraper.feeds.model import EvidenceRecord, Place
from scraper.feeds.sources.travel_updates import parse_human_datetime

FIXTURES = Path(__file__).parent / "fixtures" / "capture"


def payloads(source: str):
    out = []
    for meta_path in sorted(glob.glob(str(FIXTURES / source / "*" / "*.meta.json"))):
        p = meta_path[: -len(".meta.json")]
        meta = json.load(open(meta_path, encoding="utf-8"))
        meta.setdefault("source", source)
        out.append((Path(p).read_bytes(), meta))
    return out


def records_for(source: str):
    ad = adapters()[source]
    recs = []
    for body, meta in payloads(source):
        if ad.accepts(meta):
            recs.extend(ad.records(body, meta))
    return recs


class HelperTests(unittest.TestCase):
    def test_iso_utc_forms(self):
        self.assertEqual(iso_utc("2026-09-20T15:00:00Z"), "2026-09-20T15:00:00Z")
        self.assertEqual(iso_utc("2026-09-20T16:00:00+01:00"), "2026-09-20T15:00:00Z")
        self.assertEqual(iso_utc("Sun, 20 Sep 2026 15:25:10 +0000"), "2026-09-20T15:25:10Z")
        self.assertEqual(iso_utc(1789917910000), "2026-09-20T15:25:10Z")   # epoch ms
        self.assertIsNone(iso_utc("until further notice"))

    def test_month_bounds(self):
        self.assertEqual(month_bounds("2026-05"), ("2026-05-01T00:00:00Z", "2026-05-31T23:59:59Z"))

    def test_bng_to_wgs84_newcastle(self):
        lon, lat = bng_to_wgs84(424900, 564500)   # Grey's Monument, roughly
        self.assertAlmostEqual(lat, 54.974, places=2)
        self.assertAlmostEqual(lon, -1.613, places=2)
        g = wkt_to_geojson("LINESTRING(424900 564500, 425000 564600)", crs_bng=True)
        self.assertEqual(g["type"], "LineString")
        self.assertEqual(len(g["coordinates"]), 2)

    def test_tile_geometry_round_trip(self):
        import math
        from scraper.scrapers.roadworks import tile_geometry_to_wgs84
        lon, lat, z, extent = -1.6178, 54.9783, 10, 4096      # Newcastle city centre
        n = 2 ** z
        xf = (lon + 180.0) / 360.0 * n
        yf = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
        x, y = int(xf), int(yf)
        px, py = (xf - x) * extent, extent - (yf - y) * extent   # origin bottom-left
        g = tile_geometry_to_wgs84({"type": "Point", "coordinates": [px, py]}, z, x, y, extent)
        self.assertAlmostEqual(g["coordinates"][0], lon, places=4)
        self.assertAlmostEqual(g["coordinates"][1], lat, places=4)
        g2 = tile_geometry_to_wgs84({"type": "LineString", "coordinates": [[px, py], [px + 10, py + 10]]}, z, x, y, extent)
        self.assertEqual(len(g2["coordinates"]), 2)

    def test_place_hits_uses_gazetteer(self):
        self.assertIn("Gateshead", place_hits("Traffic chaos in Gateshead this morning"))
        self.assertEqual(place_hits("nothing here"), [])

    def test_human_datetime(self):
        self.assertEqual(parse_human_datetime("Mon 22 Sep, 9am", 2026), "2026-09-22T09:00:00Z")
        self.assertEqual(parse_human_datetime("22 September 2026 4:30pm", 2026), "2026-09-22T16:30:00Z")
        self.assertIsNone(parse_human_datetime("until further notice", 2026))

    def test_record_id_is_deterministic(self):
        a = EvidenceRecord("rss", "headline", "other", "x", {}, Place(), None, None, native_id="g1")
        b = EvidenceRecord("rss", "headline", "other", "x", {}, Place(), None, None, native_id="g1")
        self.assertEqual(a.record_id, b.record_id)
        self.assertEqual(EvidenceRecord.from_dict(a.to_dict()).record_id, a.record_id)


class AdapterGoldenTests(unittest.TestCase):
    def test_every_source_has_an_adapter(self):
        self.assertEqual(set(adapters()), {"rss", "police", "ea", "onenetwork", "bluesky", "travel_updates", "metoffice_hourly",
                                           "bods_sirisx", "uo", "nswws", "streetmanager_archive"})

    def test_rss(self):
        recs = records_for("rss")
        self.assertGreater(len(recs), 10)
        self.assertTrue(all(r.kind == "headline" and r.reliability_hint == "media" and r.text for r in recs))
        self.assertTrue(any(r.valid_from for r in recs))

    def test_police(self):
        recs = records_for("police")
        self.assertEqual(len(recs), 60)
        r = recs[0]
        self.assertEqual((r.kind, r.domain), ("crime_record", "crime"))
        self.assertEqual((r.valid_from, r.valid_to), ("2026-05-01T00:00:00Z", "2026-05-31T23:59:59Z"))
        self.assertEqual(r.place.geometry["type"], "Point")
        self.assertIn("recorded", r.rendered_text)
        self.assertIsNone(r.text)

    def test_ea(self):
        recs = records_for("ea")
        kinds = {(r.kind, r.structured.get("station") is True, "value" in r.structured) for r in recs}
        self.assertIn(("observation", True, False), kinds)     # station inventory
        self.assertIn(("observation", False, True), kinds)     # readings
        stations = [r for r in recs if r.structured.get("station") is True]
        self.assertTrue(all(r.place.geometry and r.place.geometry["type"] == "Point" for r in stations))
        readings = [r for r in recs if "value" in r.structured]
        self.assertTrue(all(r.valid_from == r.valid_to == r.observed_at for r in readings))

    def test_onenetwork(self):
        recs = records_for("onenetwork")
        self.assertEqual(len(recs), 40)
        self.assertTrue(all(r.kind == "notice" and r.domain == "roadworks" for r in recs))
        self.assertTrue(any(r.text for r in recs), "works_desc is the point of this source")
        self.assertTrue(all(r.rendered_text.startswith("Roadworks on") for r in recs))

    def test_bluesky_is_pseudonymised(self):
        recs = records_for("bluesky")
        self.assertEqual(len(recs), 3)
        blob = json.dumps([r.to_dict() for r in recs])
        self.assertNotIn('"handle"', blob)
        self.assertNotIn("did:plc:", blob.replace("at://did:plc:", ""))   # the AT-URI is the only DID-bearing field
        self.assertTrue(all(r.kind == "post" and r.reliability_hint == "social" and r.structured["author_pid"] for r in recs))

    def test_travel_updates(self):
        recs = records_for("travel_updates")
        self.assertGreater(len(recs), 5)
        self.assertTrue(all(r.kind == "notice" and r.domain == "transport" and r.native_id.startswith("tu_") for r in recs))
        self.assertTrue(all(r.valid_from for r in recs))

    def test_metoffice_hourly(self):
        recs = records_for("metoffice_hourly")
        self.assertGreater(len(recs), 20)
        r = recs[0]
        self.assertEqual((r.kind, r.domain), ("forecast", "weather"))
        self.assertTrue(r.rendered_text.startswith("Met Office forecast for"))
        self.assertTrue(r.valid_from < r.valid_to)

    def test_bods(self):
        recs = records_for("bods_sirisx")
        self.assertEqual(len(recs), 5)
        self.assertTrue(all(r.kind == "notice" and r.domain == "transport" and r.native_id for r in recs))
        self.assertTrue(any(r.valid_from and r.valid_to for r in recs))

    def test_uo(self):
        recs = records_for("uo")
        inv = [r for r in recs if r.structured.get("sensor") is True]
        data = [r for r in recs if "Value" in r.structured]
        self.assertEqual(len(inv), 40)
        self.assertGreater(len(data), 20)
        self.assertTrue(all(r.place.geometry for r in inv))
        self.assertTrue(all(r.domain == "transport" for r in data))   # Journey Time page

    def test_nswws_empty_object(self):
        # the captured object has no features (no warning in force); the adapter must return nothing, not fail
        self.assertEqual(records_for("nswws"), [])

    def test_build_and_report_over_fixtures(self):
        errors: list = []
        recs = build_records(FIXTURES.parent, errors=errors)
        self.assertGreater(len(recs), 100)
        self.assertEqual(errors, [])
        with tempfile.TemporaryDirectory() as d:
            n = write_jsonl(recs, Path(d) / "r.jsonl")
            self.assertEqual(n, len(recs))
            again = build_records(FIXTURES.parent)
            self.assertEqual([r.record_id for r in again], [r.record_id for r in recs])   # deterministic
        self.assertIn("onenetwork", report(recs))


if __name__ == "__main__":
    unittest.main()

"""Held-out export: every stage on a synthetic capture store, with LaSSI's own checker (copied unchanged) on the
result, plus the CURB template check. No real captures are used."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scraper.heldout import render
from scraper.heldout.cli import main as heldout
from scraper.heldout.docs import template_checks
from scraper.heldout.draw import MIX, allocate, claim_sheet, draw_sample

FX = Path(__file__).parent / "fixtures" / "heldout"
CHECKER_SHA256 = "7dc47309b4319630fdd1aea4b5e02cfc7c5fec9f127743d873323c4f4e5b4adc"   # LaSSI's heldout_check.py, 3 Nov
START, END = "2026-10-15T00:00:00Z", "2026-11-02T16:00:00Z"


def _put(root: Path, source: str, at: str, tag: str, body, ext: str = "json") -> str:
    day, hms = at[:10], at[11:19].replace(":", "")
    d = root / "capture" / source / day
    d.mkdir(parents=True, exist_ok=True)
    name = f"{hms}_{tag}.{ext}"
    data = body if isinstance(body, bytes) else (body.encode() if isinstance(body, str) else json.dumps(body).encode())
    (d / name).write_bytes(data)
    (d / f"{name}.meta.json").write_text(json.dumps({"fetched_at": at.replace("Z", "+00:00"), "tag": tag, "source": source, "status": 200, "file": name}))
    return f"capture/{source}/{day}/{name}"


def _works(id_, ref, pub="Newcastle City Council", tile="10/507/324", desc="Excavate to repair a leaking water main.", road="Grey Street"):
    return {"id": id_, "works_ref": ref, "pub_name": pub, "_tile": tile, "works_desc": desc, "road_name": road,
            "traffman": "Some carriageway incursion", "delay": "delays unlikely",
            "start_date": "2026-10-20T00:00:00+01:00", "end_date": "2026-10-30T23:59:00+00:00"}


NOTICE = """<div class="update-post wysiwyg p-4" data-transport="metro" data-types="{type}">
 <h2 class="mb-2 text-lg font-bold">{title}</h2>
 <div class="text-dark-teal flex items-center gap-2 font-bold"><span>Effective from: {frm}</span></div>
 <div class="mt-4"><p>{body1}<br/>{body2}</p><p><a href="https://example.org/x.pdf">View the timetable</a></p>
 <p><a href="https://travelnortheast.uk/">For more details</a></p></div>
 <span class="text-dark-teal mb-4 mt-8 block font-bold">Last updated: {upd}</span></div>"""


def _travel(root: Path, at: str, notices: list[dict]) -> None:
    from scraper.capture.sources.travel_updates import parse_updates
    html = "<html><body>" + "".join(NOTICE.format(**{"upd": n["frm"], **n}) for n in notices) + "</body></html>"
    _put(root, "travel_updates", at, "page", html, "html")
    ups = [{k: v for k, v in u.items() if k != "html"} for u in parse_updates(html)]
    _put(root, "travel_updates", at, "updates", {"url": "x", "updates": ups})


def _forecast(run: str, name: str) -> dict:
    steps = [{"time": f"2026-10-2{d}T{h:02d}:00Z", "screenTemperature": 10.5, "windSpeed10m": 3.2, "probOfPrecipitation": 20,
              "significantWeatherCode": 7} for d in (0, 1) for h in (9, 12)]
    return {"features": [{"geometry": {"coordinates": [-1.6, 54.9, 50]},
                          "properties": {"location": {"name": name}, "modelRunDate": run, "timeSeries": steps}}]}


def _crimes(city: str, month: str, n: int) -> list[dict]:
    return [{"category": "burglary", "month": month, "id": i, "persistent_id": f"{city}-{month}-{i}",
             "location": {"latitude": "54.97", "longitude": "-1.61", "street": {"id": i, "name": f"On or near Street {i}"}},
             "outcome_status": {"category": "Under investigation", "date": month}} for i in range(n)]


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    root = tmp_path / "drive"
    # roadworks: before the window (a, rescheduled b), new in the window (c, d), a dev item (e), Street Manager prior (f),
    # a new tile's baseline (g), unrenderable (h), Durham (i), outside every slice (j)
    _put(root, "onenetwork", "2026-10-01T10:00:00Z", "tiles", {"features": [_works(1, "NE1"), _works(2, "NE2"), _works(7, "DU7", "Durham", "10/507/324")]})
    _put(root, "onenetwork", "2026-10-20T10:00:00Z", "tiles", {"features": [
        _works(1, "NE1"), {**_works(2, "NE2"), "start_date": "2026-10-25T00:00:00+01:00"}, _works(3, "NE3", road="Pilgrim Street"),
        _works(4, "NE4"), _works(5, "NE5"), _works(6, "NE6"), _works(7, "DU7", "Durham"), _works(8, "DU8", "Durham", "10/507/325"),
        _works(9, "NE9", desc="short"), _works(10, "DU10", "Durham", road="Claypath"), _works(11, "SU11", "Sunderland")]})
    sm = io.BytesIO()
    with zipfile.ZipFile(sm, "w") as z:
        z.writestr("NE6-01_permit_granted_event_2026-10-02_09-00-00-0000.json", "{}")
        z.writestr("NE3-01_permit_granted_event_2026-10-19_09-00-00-0000.json", "{}")   # after the window start: still new
    _put(root, "streetmanager_archive", "2026-11-01T04:00:00Z", "permit_2026_10", sm.getvalue(), "zip")
    # transport: an old notice edited in the window (same title), a new notice, a generic title reused days later
    old = {"type": "live-update", "title": "Lift out of service at Haymarket", "frm": "10 Oct 2026, 08:00",
           "body1": "The lift at Haymarket is out of service", "body2": "Use the help point"}
    _travel(root, "2026-10-10T08:05:00Z", [old])
    _travel(root, "2026-10-20T08:05:00Z", [{**old, "body2": "Engineers are on site"},
                                           {"type": "planned-work", "title": "Line closure Pelaw to South Shields", "frm": "24 Oct 2026, 05:00",
                                            "body1": "No trains will run between Pelaw and South Shields", "body2": "Replacement buses will run",
                                            "upd": "19 Oct 2026, 16:40"}])
    # weather: two runs per city in the window, one before
    for at, run in (("2026-10-10T09:00:00Z", "2026-10-10T08:00Z"), ("2026-10-20T09:00:00Z", "2026-10-20T08:00Z"), ("2026-10-20T10:00:00Z", "2026-10-20T09:00Z")):
        _put(root, "metoffice_hourly", at, "hourly_newcastle", _forecast(run, "Newcastle Upon Tyne"))
        _put(root, "metoffice_hourly", at, "hourly_durham", _forecast(run, "Durham"))
    # crime: Newcastle August (captured before the window) and September (in it); Durham August (before the window)
    _put(root, "police", "2026-10-01T10:00:00Z", "newcastle_centre_2026-08", _crimes("ncl", "2026-08", 3))
    _put(root, "police", "2026-10-21T10:00:00Z", "newcastle_centre_2026-09", _crimes("ncl", "2026-09", 4))
    _put(root, "police", "2026-10-01T10:00:00Z", "durham_2026-08", _crimes("dur", "2026-08", 5))
    return root


@pytest.fixture()
def dev(tmp_path: Path, store: Path) -> Path:
    from scraper.feeds.build import build_records
    rid = next(r.record_id for r in build_records(store, {"onenetwork"}) if r.structured["id"] == 4)
    p = tmp_path / "official_sample.json"
    p.write_text(json.dumps([{"source": "onenetwork", "record_id": rid}]))
    return p


def _pool(tmp_path, store, dev, *extra):
    out = tmp_path / "build"
    assert heldout(["records", "--root", str(store), "--out-dir", str(out), "--before", END]) == 0
    assert heldout(["pool", "--root", str(store), "--out-dir", str(out), "--window-start", START, "--window-end", END,
                    "--curb", str(FX / "curb_sample.csv"), "--dev", str(dev), *extra]) == 0
    rows = list(csv.DictReader((out / "01_pool/pool.csv").open(encoding="utf-8")))
    meta = json.loads((out / "01_pool/pool.csv.meta.json").read_text())
    return out, rows, meta


def test_checker_copy_is_lassis_unchanged():
    assert hashlib.sha256((FX / "heldout_check.py").read_bytes()).hexdigest() == CHECKER_SHA256


def test_pool_eligibility_and_every_exclusion(tmp_path, store, dev):
    out, rows, meta = _pool(tmp_path, store, dev)
    c = meta["counts"]["counts"]
    keys = {(r["domain"], r["city"]): [] for r in rows}
    for r in rows:
        keys[(r["domain"], r["city"])].append(r)
    # NE1 and NE2 (rescheduled) existed before the window; NE3 and NE5 are new (same text, distinct works)
    assert {r["works_ref"] for r in keys[("roadworks", "newcastle")]} == {"NE3", "NE5"}
    assert c["dev"] == {"roadworks/newcastle": 1}
    assert c["street_manager"] == {"roadworks/newcastle": 1}           # NE6: permit event before the window start
    assert c["baseline"] == {"roadworks/durham": 1}                    # DU8: first seen in the new tile's first capture
    assert c["not_renderable"] == {"roadworks/newcastle": 1}           # NE9: description too short
    assert {r["works_ref"] for r in keys[("roadworks", "durham")]} == {"DU10"}
    assert c["curb"] == {}
    assert c["eligible"]["roadworks/newcastle"] == 5 and c["pool"]["roadworks/newcastle"] == 2
    assert c["in_scope"] == {"crime/durham": 5, "crime/newcastle": 7, "roadworks/durham": 3, "roadworks/newcastle": 7,
                             "transport/newcastle": 2, "weather/durham": 12, "weather/newcastle": 12}
    ne3 = next(r for r in rows if r["works_ref"] == "NE3")
    assert ne3["evidence_text"] == ("Roadworks on Pilgrim Street: Excavate to repair a leaking water main.. Traffic management: "
                                    "Some carriageway incursion. Delays unlikely. Work expected to end 2026-10-30.")
    assert (ne3["date_time"], ne3["location"], ne3["source_page"]) == ("2026-10-20T00:00:00+01:00", "Pilgrim Street", "Roadworks in Newcastle Map (One.Network)")
    # transport: the edited old notice is not new; the planned closure is
    tr = keys[("transport", "newcastle")]
    assert len(tr) == 1 and tr[0]["evidence_text"] == ("Line closure Pelaw to South Shields. Effective from: 24 Oct 2026, 05:00. "
                                                       "No trains will run between Pelaw and South Shields. Replacement buses will run.")
    assert tr[0]["date_time"] == "2026-10-19T16:40:00+01:00" and tr[0]["location"] == "South Shields" and tr[0]["source_name"] == "Nexus Metro Updates"
    # crime: Newcastle September only; Durham's August counts (captured before the window, durham_crime_all_captured)
    assert {r["month"] for r in keys[("crime", "newcastle")]} == {"2026-09"} and len(keys[("crime", "newcastle")]) == 4
    assert len(keys[("crime", "durham")]) == 5
    cr = keys[("crime", "durham")][0]
    assert cr["evidence_text"].startswith("Burglary recorded on On Or Near Street ") and ", Durham, in August 2026. Outcome: Under investigation." in cr["evidence_text"]
    assert cr["location"].startswith("On or near Street ") and cr["date_time"] == "2026-08-01T00:00:00+00:00"
    # weather: two in-window runs x 4 steps per city, canonical place names
    w = keys[("weather", "newcastle")]
    assert len(w) == 8 and {r["model_run"] for r in w} == {"2026-10-20T08:00Z", "2026-10-20T09:00Z"}
    assert w[0]["evidence_text"].startswith("Met Office forecast for Newcastle upon Tyne: Cloudy, 10.5°C, wind 3.2 m/s, 20% chance")
    assert keys[("weather", "durham")][0]["source_page"] == "Durham Hourly Forecast"
    assert all(r["item_key"] and r["first_seen"] >= START or r["domain"] == "crime" for r in rows)


def test_pool_settings_switch_rules(tmp_path, store, dev):
    _, rows, meta = _pool(tmp_path, store, dev, "--no-durham-crime-all-captured", "--no-street-manager", "--max-evidence-chars", "120")
    assert rows and all(len(r["evidence_text"]) <= 120 for r in rows)
    assert meta["counts"]["counts"]["too_long"]["transport/newcastle"] == 1          # dropped, not cut
    assert meta["counts"]["counts"]["date_time_fallback"] == {}
    assert not [r for r in rows if (r["domain"], r["city"]) == ("crime", "durham")]   # August was not served in the window
    assert "street_manager" not in json.dumps(meta["counts"]["counts"]["street_manager"]) and meta["counts"]["counts"]["street_manager"] == {}
    assert meta["settings"]["durham_crime_all_captured"] is False


def test_sample_and_claims_are_seeded_and_mirror_curb(tmp_path, store, dev):
    out, rows, _ = _pool(tmp_path, store, dev)
    a, rep = draw_sample(rows, seed=7)
    b, _ = draw_sample(rows, seed=7)
    assert [r["item_key"] for r in a] == [r["item_key"] for r in b]
    w = [r for r in a if r["domain"] == "weather" and r["city"] == "newcastle"]
    assert len({r["model_run"] for r in w}) == len(w) == 2                     # one step per model run
    assert rep["crime/newcastle"] == {"target": 17, "available": 4, "drawn": 4}
    assert all(r["item_id"].startswith(f"test_{r['city']}_{r['domain']}_") for r in a)
    sheet, srep = claim_sheet(a, seed=7)
    assert all(r["S_label"] == "Supported" and r["R_label"] == "Refuted" and r["N_label"] == "Not Enough Evidence" and r["S_claim"] == "" for r in sheet)
    assert sum(srep["crime"]["R"].values()) == len([r for r in a if r["domain"] == "crime"])
    assert allocate(MIX["transport"]["N"], 8) == ["cause_mismatch"] * 5 + ["time_mismatch"] + ["unsupported_extra_detail"] * 2
    assert sorted(allocate(MIX["crime"]["R"], 17)) == sorted(["negation"] * 5 + ["location_mismatch"] * 5 + ["outcome_mismatch"] * 7)


def test_heldout_csv_passes_lassis_checker(tmp_path, store, dev):
    out, _, _ = _pool(tmp_path, store, dev)
    assert heldout(["sample", "--out-dir", str(out), "--seed", "11"]) == 0
    assert heldout(["claims", "--out-dir", str(out), "--seed", "11"]) == 0
    assert heldout(["heldout", "--out-dir", str(out)]) == 1                       # claims not written yet: refused
    sheet = list(csv.DictReader((out / "03_claims/claim_sheet.csv").open(encoding="utf-8")))
    for r in sheet:
        for slot in "SRN":
            r[f"{slot}_claim"] = f"test claim {slot}"
    filled = tmp_path / "filled.csv"
    with filled.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(sheet[0])); w.writeheader(); w.writerows(sheet)
    assert heldout(["heldout", "--out-dir", str(out), "--sheet", str(filled), "--check", str(FX / "heldout_check.py")]) == 0
    res = subprocess.run([sys.executable, str(FX / "heldout_check.py"), str(out / "03_claims/heldout.csv")], capture_output=True, text=True)
    assert res.returncode == 0 and f"{len(sheet)} items, {3 * len(sheet)} pairs" in res.stdout
    hdr = next(csv.reader((out / "03_claims/heldout.csv").open(encoding="utf-8")))
    assert hdr[:13] == ["item_id", "domain", "evidence_text", "source_name", "source_page", "source_url", "date_time", "location",
                        "claim_text", "label", "claim_type", "city", "record_id"]
    meta = json.loads((out / "03_claims/heldout.csv.meta.json").read_text())
    assert meta["counts"]["check"]["exit"] == 0
    assert heldout(["docs", "--out-dir", str(out), "--curb", str(FX / "curb_sample.csv")]) == 0
    assert "seed" in (out / "README.md").read_text() and "| crime | crime_001 | identical |" in (out / "DATA_DICTIONARY.md").read_text()


def test_checker_rejects_a_broken_file(tmp_path):
    bad = tmp_path / "bad.csv"
    rows = list(csv.DictReader((FX / "curb_sample.csv").open(encoding="utf-8")))
    rows = [r for r in rows if not r["item_id"].endswith("_N")]
    with bad.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    res = subprocess.run([sys.executable, str(FX / "heldout_check.py"), str(bad)], capture_output=True, text=True)
    assert res.returncode == 1 and "needs S, R and N" in res.stderr


def test_template_check_reproduces_curb():
    res = template_checks(FX / "curb_sample.csv")
    for domain in ("crime", "roadworks"):
        assert [r["result"] for r in res[domain]] == ["identical"] * 5, res[domain]
    assert [r["result"] for r in res["weather"]] == ["identical apart from the wind unit"] * 5, res["weather"]
    assert all(r["result"] == "differs by design" for r in res["transport"])


def test_transport_rendering_keeps_line_breaks():
    parts = render.notice_lines(NOTICE.format(type="live-update", title="Delays at Monument", frm="2 Nov 2026, 07:00", upd="2 Nov 2026, 07:05",
                                              body1="Trains are delayed", body2="Please allow extra time!"))
    assert parts["lines"] == ["Trains are delayed", "Please allow extra time!"]
    assert render.transport_text(parts) == "Delays at Monument. Effective from: 2 Nov 2026, 07:00. Trains are delayed. Please allow extra time!"
    assert render.page_time("2 Nov 2026, 07:00", 2026) == "2026-11-02T07:00:00+00:00"


def test_out_dir_inside_google_drive_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        heldout(["sample", "--out-dir", str(tmp_path / "Library" / "CloudStorage" / "x"), "--seed", "1"])


def test_transport_date_time_is_last_updated_else_first_seen():
    parts = {"title": "Lift out of service", "from": None, "until": None, "lines": ["Use the help point"]}
    rec = {"structured": {"last_updated": "8 Oct 2026, 12:12"}, "first_seen": "2026-10-08T10:00:00Z", "fetched_at": "2026-10-08T10:00:00Z"}
    row = render.transport(rec, parts)
    assert (row["date_time"], row["date_time_fallback"]) == ("2026-10-08T12:12:00+01:00", False)
    row = render.transport({**rec, "structured": {}}, parts)
    assert (row["date_time"], row["date_time_fallback"]) == ("2026-10-08T11:00:00+01:00", True)    # first_seen, in UK time


def test_v1_weather_sentence_is_unchanged():
    from scraper.scrapers.metoffice import format_forecast_evidence
    step = {"time": "2026-04-13T11:00Z", "screenTemperature": 10.42, "windSpeed10m": 2.8, "probOfPrecipitation": 1, "significantWeatherCode": 1}
    assert format_forecast_evidence(step) == ("Met Office forecast for Newcastle upon Tyne: Sunny day, 10.42°C, wind 2.8 mph, "
                                              "1% chance of precipitation at 2026-04-13T11:00Z.")       # CURB weather_001

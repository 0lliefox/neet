import json
from pathlib import Path

from scraper.capture.store import Store, Payload
from scraper.capture.state import State
from scraper.capture.sources.bluesky import load_lexicon, build_s1_queries, Matcher


def test_store_writes_payload_and_meta(tmp_path: Path):
    st = Store(tmp_path)
    p = st.write("rss", Payload(tag="bbc tyne", body=b"<rss/>", ext="xml", meta={"url": "u", "status": 200}, n_items=3))
    assert p.exists() and p.suffix == ".xml"
    meta = json.loads((p.parent / (p.name + ".meta.json")).read_text())
    assert meta["source"] == "rss" and meta["n_items"] == 3 and meta["sha256"]
    st.health({"source": "rss", "ok": True})
    assert st.last_health()["rss"]["ok"] is True


def test_state_due_and_save(tmp_path: Path):
    s = State(tmp_path, "x")
    assert s.due(60)
    s.mark_run(); s.save()
    s2 = State(tmp_path, "x")
    assert not s2.due(3600)


def test_s1_queries_have_exclusions_and_no_or():
    lex = load_lexicon()
    qs = build_s1_queries(lex)
    assert len(qs) > 50
    for place, q in qs:
        assert " OR " not in q and "-NUFC" in q
        assert place.split()[0] in q


def test_matcher_place_and_topic_rules():
    lex = load_lexicon()
    m = Matcher(lex)
    post = {"record": {"text": "Coast Road flooded again near Wallsend, buses diverted"}}
    a = m.analyse(post)
    assert a["place_ok"] and "flood" in a["topic_hits"] and "transport" in a["topic_hits"]
    generic_only = {"record": {"text": "Newcastle are top of the league"}}
    a2 = m.analyse(generic_only)
    assert not a2["place_ok"] and not a2["topic_hits"]
    generic_with_topic = {"record": {"text": "Newcastle: A1 closed after crash, huge delays"}}
    a3 = m.analyse(generic_with_topic)
    assert a3["place_ok"]
    linkcard = {"record": {"text": ""}, "embed": {"external": {"title": "Flood alert issued for the Tyne at Newburn"}}}
    assert m.analyse(linkcard)["place_ok"]


def test_generic_names_are_not_strong_cues_or_queries():
    lex = load_lexicon()
    m = Matcher(lex)
    for txt in ["University lecture was great", "Monument was busy", "Nexus 7 tablet for sale", "the Quayside in Bristol"]:
        assert not m.analyse({"record": {"text": txt}})["place_ok"], txt
    assert all(place not in set(lex["places"]["tier3_generic_needs_cue"]) for place, _ in build_s1_queries(lex))
    assert m.analyse({"record": {"text": "Haymarket Metro closed, no trains"}})["place_ok"]


def test_bluesky_run_dedups_by_uri(tmp_path):
    from scraper.capture.sources.bluesky import BlueskySource
    from scraper.capture.store import Store
    from scraper.capture.state import State
    from scraper.capture.base import Context
    import requests, json
    src = BlueskySource({})
    st = Store(tmp_path)
    ctx = Context(store=st, state=State(st.state_dir, "bluesky"), config={}, env={"PSEUDONYM_SALT": "s"}, session=requests.Session())
    src._run_seen, src._pending = {}, []
    post = {"uri": "at://x/1", "author": {"did": "did:plc:a"}, "record": {"text": "Coast Road flooded near Wallsend"}}
    p1, s1 = src._emit(ctx, "S1-KW_Wallsend", "S1-KW", [post], set(), extra={"q": "Wallsend"}, require_place=True)
    p2, s2 = src._emit(ctx, "S1-KW_Coast Road", "S1-KW", [post], set(), extra={"q": "Coast Road"}, require_place=True)
    p3, s3 = src._emit(ctx, "S5-HASH_Newcastle", "S5-HASH", [post], set(), extra={"tag": "Newcastle"})
    assert s1["kept"] == 1 and s2["kept"] == 0 and s2["dupes"] == 1 and s3["dupes"] == 1
    for pl, doc in src._pending:
        pl.body = json.dumps(doc).encode()
    d1 = json.loads(p1.body)
    assert d1["posts"][0]["strategies"] == ["S1-KW", "S5-HASH"] and len(d1["posts"][0]["matched_by"]) == 3
    assert json.loads(p2.body)["dupes"] == ["at://x/1"]


def test_store_skips_unchanged_repeat(tmp_path):
    st = Store(tmp_path)
    p = Payload(tag="siri", body=b"<Siri/>", ext="xml")
    assert st.write("bods", p) is not None
    assert st.write("bods", Payload(tag="siri", body=b"<Siri/>", ext="xml")) is None
    assert st.write("bods", Payload(tag="siri", body=b"<Siri>x</Siri>", ext="xml")) is not None


def test_parse_travel_updates_fixture():
    from scraper.capture.sources.travel_updates import parse_updates
    html = '''<div><div class="p-4 update-post wysiwyg"><h3>Fellgate lift - out of service</h3><p>Effective from: 18 Sep 2026, 08:30</p>
    <p>The lift at Fellgate on platform 1 is out of service until Tuesday.</p><p>Last updated: 18 Sep 2026, 08:30</p></div>
    <div class="hidden p-4 update-post wysiwyg"><h3>Resurfacing works, A184, Gateshead – Disruption to Services 21, 27</h3>
    <p>Effective from: 17 Sep 2026, 20:00</p><p>Effective until: 25 Sep 2026, 06:00</p><p>Buses will divert via the Felling Bypass.</p></div></div>'''
    ups = parse_updates(html)
    assert len(ups) == 2
    assert ups[0]["title"].startswith("Fellgate lift") and ups[0]["effective_from"] == "18 Sep 2026, 08:30" and ups[0]["mode_guess"] == "metro"
    assert ups[0]["last_updated"] == "18 Sep 2026, 08:30" and "platform 1" in ups[0]["body"]
    assert ups[1]["hidden"] is True and ups[1]["effective_until"] == "25 Sep 2026, 06:00" and ups[1]["mode_guess"] == "bus"


class _FakeSession:
    """Records GET calls and answers each from `reply(url, params) -> (status, body)`."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def get(self, url, params=None, headers=None, timeout=None):
        import requests
        self.calls.append((url, dict(params or {})))
        status, body = self.reply(url, params or {})
        r = requests.Response()
        r.status_code, r._content, r.url = status, body, url
        r.headers["Content-Type"] = "application/json"
        return r


def _ctx(tmp_path, source, session, env=None):
    from scraper.capture.base import Context
    st = Store(tmp_path)
    return Context(store=st, state=State(st.state_dir, source), config={}, env=env or {}, session=session)


def test_metoffice_hourly_tags_one_payload_per_point(tmp_path):
    from scraper.capture.sources.metoffice_hourly import MetOfficeHourlySource
    sess = _FakeSession(lambda url, p: (200, b'{"features": []}'))
    src = MetOfficeHourlySource({"points": [{"lat": 54.9781, "lng": -1.6162, "name": "newcastle"},
                                            {"lat": 54.7765, "lng": -1.5757, "name": "durham"}]})
    out = src.fetch(_ctx(tmp_path, "metoffice_hourly", sess, {"METOFFICE_DATAHUB_KEY": "k"}))
    assert [p.tag for p in out] == ["hourly_newcastle", "hourly_durham"]
    assert [c[1]["latitude"] for c in sess.calls] == [54.9781, 54.7765]


def test_metoffice_hourly_without_points_keeps_the_newcastle_tag(tmp_path):
    from scraper.capture.sources.metoffice_hourly import MetOfficeHourlySource
    sess = _FakeSession(lambda url, p: (200, b'{"features": []}'))
    out = MetOfficeHourlySource({"latitude": 54.9781, "longitude": -1.6162}).fetch(
        _ctx(tmp_path, "metoffice_hourly", sess, {"METOFFICE_DATAHUB_KEY": "k"}))
    assert [p.tag for p in out] == ["hourly_newcastle"]
    assert sess.calls[0][1]["latitude"] == 54.9781 and sess.calls[0][1]["longitude"] == -1.6162


def test_police_tracks_done_months_per_point(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import scraper.capture.sources.police_monthly as pm
    monkeypatch.setattr(pm, "utcnow", lambda: datetime(2026, 10, 11, tzinfo=timezone.utc))
    # Aug and Jul are published; Sep is not yet (empty list); Jun is a 404.
    published = {"2026-08": b'[{"id": 1}]', "2026-07": b'[{"id": 2}]', "2026-09": b"[]"}
    sess = _FakeSession(lambda url, p: (200, published[p["date"]]) if p["date"] in published else (404, b""))
    newcastle = {"lat": 54.9781, "lng": -1.6162, "name": "newcastle_centre"}
    durham = {"lat": 54.7765, "lng": -1.5757, "name": "durham"}
    ctx = _ctx(tmp_path, "police", sess)
    fast = {"pause_s": 0, "retry_s": 0}
    pm.PoliceMonthlySource({"points": [newcastle], **fast}).fetch(ctx)
    assert ctx.state.get("months_done_by_point") == {"newcastle_centre": ["2026-07", "2026-08"]}
    # Adding a point later backfills the months Newcastle already has, and re-asks Newcastle only for Sep and Jun.
    sess.calls.clear()
    out = pm.PoliceMonthlySource({"points": [newcastle, durham], **fast}).fetch(ctx)
    asked = sorted((c[1]["lat"], c[1]["date"]) for c in sess.calls)
    assert asked == sorted([(54.9781, "2026-09"), (54.9781, "2026-06"),
                            (54.7765, "2026-09"), (54.7765, "2026-08"), (54.7765, "2026-07"), (54.7765, "2026-06")])
    assert {p.tag for p in out} >= {"durham_2026-08", "durham_2026-07"}
    assert ctx.state.get("months_done_by_point") == {"newcastle_centre": ["2026-07", "2026-08"],
                                                     "durham": ["2026-07", "2026-08"]}


def test_police_retries_a_rate_limited_request(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import scraper.capture.sources.police_monthly as pm
    monkeypatch.setattr(pm, "utcnow", lambda: datetime(2026, 10, 11, tzinfo=timezone.utc))
    answers = {"2026-08": [(429, b""), (429, b""), (200, b'[{"id": 1}]')]}
    sess = _FakeSession(lambda url, p: answers[p["date"]].pop(0) if p["date"] in answers else (404, b""))
    ctx = _ctx(tmp_path, "police", sess)
    out = pm.PoliceMonthlySource({"points": [{"lat": 1, "lng": 2, "name": "x"}], "pause_s": 0, "retry_s": 0}).fetch(ctx)
    assert [(p.tag, p.meta["status"]) for p in out] == [("x_2026-08", 200)]
    assert ctx.state.get("months_done_by_point") == {"x": ["2026-08"]}


def test_store_treats_an_unreadable_hash_file_as_changed(tmp_path, monkeypatch):
    # Google Drive for desktop can refuse a read with EDEADLK ("Resource deadlock avoided").
    import errno, pathlib
    st = Store(tmp_path)
    assert st.write("ea", Payload(tag="floods_x", body=b"{}")) is not None
    real = pathlib.Path.read_text

    def flaky(self, *a, **k):
        if self.suffix == ".sha256":
            raise OSError(errno.EDEADLK, "Resource deadlock avoided")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", flaky)
    assert st.unchanged("ea", Payload(tag="floods_x", body=b"{}")) is False
    assert st.write("ea", Payload(tag="floods_x", body=b"{}")) is not None   # written again, not raised
    monkeypatch.setattr(pathlib.Path, "read_text", real)
    assert st.write("ea", Payload(tag="floods_x", body=b"{}")) is None       # hash file rewritten and readable


def test_capture_run_survives_one_unwritable_payload(tmp_path, monkeypatch):
    import scraper.capture.cli as cli
    from scraper.capture.base import CaptureSource

    class Two(CaptureSource):
        name = "two"

        def fetch(self, ctx):
            return [Payload(tag="bad", body=b"1", meta={"status": 200}), Payload(tag="good", body=b"2", meta={"status": 200})]

    real_write = Store.write

    def write(self, source, payload, *a, **k):
        if payload.tag == "bad":
            raise OSError(11, "Resource deadlock avoided")
        return real_write(self, source, payload, *a, **k)

    monkeypatch.setattr(cli, "all_sources", lambda cfg: [Two({})])
    monkeypatch.setattr(Store, "write", write)
    assert cli.main(["--data-root", str(tmp_path), "--force"]) == 0
    rec = Store(tmp_path).last_health()["two"]
    assert rec["ok"] is False and rec["n_written"] == 1 and rec["n_write_errors"] == 1
    assert list((tmp_path / "capture" / "two").glob("*/*_good.json"))


def test_uo_stops_after_consecutive_gateway_errors(tmp_path):
    from scraper.capture.sources.uo import UOSource
    sess = _FakeSession(lambda url, p: (504, b"<html>504</html>"))
    ctx = _ctx(tmp_path, "uo", sess)
    ctx.state.set("inventory_at", "2999-01-01T00:00:00+00:00")   # inventory not due
    src = UOSource({"variables": ["A", "B", "C", "D", "E"], "lookback_days": 2, "max_consecutive_gateway_errors": 3})
    out = src.fetch(ctx)
    assert len(sess.calls) == 3 and len(out) == 3
    assert all(v == [] for v in ctx.state.get("days_done").values())


def test_state_retries_failed_runs_sooner_with_backoff(tmp_path: Path):
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    s = State(tmp_path, "daily")
    s.mark_run(now=t0, ok=True)
    assert not s.due(86400, now=t0 + timedelta(hours=23))
    s.mark_run(now=t0, ok=False)
    assert s.fail_streak() == 1
    assert not s.due(86400, now=t0 + timedelta(seconds=500))
    assert s.due(86400, now=t0 + timedelta(seconds=540))
    s.mark_run(now=t0, ok=False)
    assert s.fail_streak() == 2
    assert not s.due(86400, now=t0 + timedelta(seconds=1000)) and s.due(86400, now=t0 + timedelta(seconds=1080))
    for _ in range(20):
        s.mark_run(now=t0, ok=False)
    assert not s.due(86400, now=t0 + timedelta(hours=23)) and s.due(86400, now=t0 + timedelta(hours=24))
    s.mark_run(now=t0, ok=True)
    assert s.fail_streak() == 0 and not s.due(86400, now=t0 + timedelta(hours=1))


def test_state_written_before_streaks_counts_last_failure(tmp_path: Path):
    (tmp_path / "old.json").write_text(json.dumps({"last_run": "2026-10-01T12:00:00+00:00",
                                                    "last_success": "2026-09-30T12:00:00+00:00"}))
    s = State(tmp_path, "old")
    assert s.fail_streak() == 1
    s.mark_run(ok=False)
    assert s.fail_streak() == 2


def _rec(at, source="ea", **kw):
    return {"at": at, "source": source, **kw}


def test_outages_span_network_failures_and_ignore_empty_oks():
    from scraper.capture.report import outages
    dns = "ConnectionError: ... (Caused by NameResolutionError(\"HTTPSConnection(host='x', port=443): Failed to resolve\"))"
    recs = [_rec("2026-10-16T18:00:00+00:00", ok=True, n_items=5, statuses=[200]),
            _rec("2026-10-16T18:10:00+00:00", ok=False, error=dns),
            _rec("2026-10-16T18:20:00+00:00", source="onenetwork", ok=True, n_items=0, statuses=[None]),
            _rec("2026-10-16T19:00:00+00:00", source="_run", ok=False, network="down"),
            _rec("2026-10-16T19:30:00+00:00", ok=False, error=dns),
            _rec("2026-10-16T19:40:00+00:00", ok=True, n_items=3, statuses=[200]),
            _rec("2026-10-16T20:00:00+00:00", ok=False, error=dns)]  # too short to count
    spans = outages(recs)
    assert len(spans) == 1
    a, b, ongoing = spans[0]
    assert a.hour == 18 and a.minute == 10 and b.hour == 19 and b.minute == 40 and not ongoing


def test_report_flags_stale_sources_and_says_all_good(tmp_path: Path):
    from datetime import datetime, timedelta, timezone
    from scraper.capture.base import CaptureSource
    from scraper.capture.report import build_report

    class Src(CaptureSource):
        def __init__(self, name, cadence):
            super().__init__({"cadence_s": cadence}); self.name = name

    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    st = Store(tmp_path)
    fresh, old = State(st.state_dir, "fresh"), State(st.state_dir, "old")
    fresh.mark_run(now=now - timedelta(minutes=5)); fresh.save()
    old.mark_run(now=now - timedelta(days=3)); old.mark_run(now=now - timedelta(minutes=5), ok=False); old.save()
    st.health({"source": "_run", "ok": True, "network": "ok"})
    st.health({"source": "old", "ok": False, "error": "ConnectionError: (Caused by NameResolutionError(\"host='data.police.uk'\"))"})
    rep = build_report(st, [Src("fresh", 3600), Src("old", 86400)], now=datetime.now(timezone.utc))
    assert "1 PROBLEM: old" in rep and "DNS lookup failed (data.police.uk)" in rep
    rep = build_report(st, [Src("fresh", 3600)], now=datetime.now(timezone.utc))
    assert "ALL GOOD" in rep


def test_capture_skips_run_when_network_down(tmp_path: Path, monkeypatch):
    from scraper.capture import cli
    monkeypatch.setattr(cli, "_network_problem", lambda hosts: "www.gov.uk: no route")
    monkeypatch.setattr(cli, "notify", lambda *a: None)
    assert cli.main(["--data-root", str(tmp_path)]) == 0
    st = Store(tmp_path)
    assert st.last_health()["_run"]["network"] == "down"
    assert not [p for p in st.state_dir.glob("*.json") if not p.name.startswith("_")]  # no source schedule touched
    assert (st.logs_dir / "STATUS.txt").read_text().startswith("NEET capture status")


def test_capture_does_not_create_missing_drive_folder(tmp_path: Path, monkeypatch):
    from scraper.capture import cli
    monkeypatch.setattr(cli, "NO_ROOT_MARKER", tmp_path / "marker")
    sent = []
    monkeypatch.setattr(cli, "notify", lambda *a: sent.append(a))
    root = tmp_path / "GoogleDrive" / "My Drive" / "Data"
    assert cli.main(["--data-root", str(root)]) == 1
    assert not root.parent.exists() and not sent        # first miss: maybe Drive is still mounting
    assert cli.main(["--data-root", str(root)]) == 1
    assert len(sent) == 1                               # still missing ten minutes later: tell the user once
    cli.main(["--data-root", str(root)])
    assert len(sent) == 1


def test_redact_hides_query_keys_and_secret_env_values(monkeypatch):
    from scraper.capture.redact import redact
    monkeypatch.setenv("SOME_API_KEY", "s3cr3tvalue123")
    monkeypatch.setenv("HOME_DIR_NAME", "notasecret99")
    s = redact("GET /siri-sx/?api_key=abc123&x=1 failed; header had s3cr3tvalue123; monkey=ok; notasecret99")
    assert "abc123" not in s and "api_key=REDACTED&x=1" in s
    assert "s3cr3tvalue123" not in s
    assert "monkey=ok" in s and "notasecret99" in s


def test_health_records_are_redacted(tmp_path: Path):
    st = Store(tmp_path)
    st.health({"source": "bods_sirisx", "ok": False,
               "error": "ConnectionError: HTTPSConnectionPool(host='x'): url: /api/v1/siri-sx/?api_key=7a62020b8b (...)"})
    line = (st.logs_dir / "health.jsonl").read_text()
    assert "7a62020b8b" not in line and "api_key=REDACTED" in line


def test_police_recaptures_durham_months_once_after_the_tag(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import scraper.capture.sources.police_monthly as pm
    published = {"2026-09": b'[{"id": 9}]', "2026-08": b'[{"id": 8}]'}
    sess = _FakeSession(lambda url, p: (200, published[p["date"]]) if p["date"] in published else (404, b""))
    newcastle = {"lat": 54.9781, "lng": -1.6162, "name": "newcastle_centre"}
    durham = {"lat": 54.7765, "lng": -1.5757, "name": "durham"}
    cfg = {"points": [newcastle, durham], "pause_s": 0, "retry_s": 0,
           "recapture": {"after": "2026-10-25T12:00:00Z", "points": ["durham"]}}
    ctx = _ctx(tmp_path, "police", sess)
    monkeypatch.setattr(pm, "utcnow", lambda: datetime(2026, 10, 20, tzinfo=timezone.utc))
    out = pm.PoliceMonthlySource(cfg).fetch(ctx)           # before the tag: an ordinary run
    assert not any(p.keep_duplicate for p in out) and len(out) == 4
    for p in out:
        ctx.store.write("police", p)
    sess.calls.clear()
    monkeypatch.setattr(pm, "utcnow", lambda: datetime(2026, 10, 26, tzinfo=timezone.utc))
    out = pm.PoliceMonthlySource(cfg).fetch(ctx)           # after the tag: Durham only, stored although unchanged
    assert sorted(p.tag for p in out) == ["durham_2026-08", "durham_2026-09"]
    assert all(p.keep_duplicate and p.meta["recapture_after"] == "2026-10-25T12:00:00+00:00" for p in out)
    assert all(ctx.store.write("police", p, skip_unchanged=not p.keep_duplicate) is not None for p in out)
    sess.calls.clear()
    assert pm.PoliceMonthlySource(cfg).fetch(ctx) == []     # once only: published months are not asked again
    assert not [c for c in sess.calls if c[1]["date"] in published]


def test_compressed_payload_round_trips_and_still_dedups(tmp_path: Path):
    from scraper.capture.store import read_payload
    st = Store(tmp_path)
    body = b"<Siri>" + b"<PtSituationElement/>" * 500 + b"</Siri>"
    p = st.write("bods_sirisx", Payload(tag="siri_sx", body=body, ext="xml", meta={"status": 200}, compress=True))
    assert p.name.endswith(".xml.gz") and p.stat().st_size < len(body)
    assert read_payload(p) == body
    meta = json.loads((p.parent / (p.name + ".meta.json")).read_text())
    assert meta["content_encoding"] == "gzip" and meta["bytes"] == len(body) and meta["file"] == p.name
    assert st.write("bods_sirisx", Payload(tag="siri_sx", body=body, ext="xml", compress=True)) is None


def test_feeds_build_reads_compressed_bods_payloads(tmp_path: Path):
    import glob
    from scraper.feeds.build import build_records
    fx = Path(__file__).parent / "fixtures" / "capture" / "bods_sirisx"
    xml = Path(glob.glob(str(fx / "*" / "*_siri_sx.xml"))[0])
    meta = json.loads(Path(str(xml) + ".meta.json").read_text())
    plain, packed = tmp_path / "plain", tmp_path / "packed"
    for root, compress in ((plain, False), (packed, True)):
        Store(root).write("bods_sirisx", Payload(tag="siri_sx", body=xml.read_bytes(), ext="xml",
                                                 meta={k: meta[k] for k in ("url", "status") if k in meta}, compress=compress))
    a = [r.record_id for r in build_records(plain, {"bods_sirisx"})]
    b = [r.record_id for r in build_records(packed, {"bods_sirisx"})]
    assert a and a == b

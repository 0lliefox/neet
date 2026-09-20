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

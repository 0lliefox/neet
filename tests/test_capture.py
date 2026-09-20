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

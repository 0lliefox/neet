"""Reddit source (official Data API, app-only OAuth) and adapter, against a fake API."""
import json

from scraper.capture.base import Context
from scraper.capture.state import State
from scraper.capture.store import Store


class _Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _listing(children, after=None):
    return {"data": {"after": after, "children": children}}


class FakeSession:
    def __init__(self):
        self.gets = []

    def post(self, url, auth=None, data=None, headers=None, timeout=None):
        assert data == {"grant_type": "client_credentials"} and auth == ("id", "secret")
        return _Resp({"access_token": "tok"})

    def get(self, url, params=None, headers=None, timeout=None):
        self.gets.append(url)
        assert headers["Authorization"] == "bearer tok"
        if url.endswith("/r/NewcastleUponTyne/new"):
            return _Resp(_listing([
                {"kind": "t3", "data": {"id": "a1", "name": "t3_a1", "subreddit": "NewcastleUponTyne", "author": "someone",
                                         "author_fullname": "t2_x", "created_utc": 1790000000, "num_comments": 1,
                                         "title": "Metro not running between Haymarket and Jesmond?", "selftext": ""}},
                {"kind": "t3", "data": {"id": "a2", "name": "t3_a2", "subreddit": "NewcastleUponTyne", "author": "other",
                                         "created_utc": 1789999000, "num_comments": 0, "title": "Best pizza in town", "selftext": ""}}]))
        if url.endswith("/r/NewcastleUponTyne/comments/a1"):
            return _Resp([_listing([]), _listing([
                {"kind": "t1", "data": {"id": "c1", "name": "t1_c1", "author": "third", "created_utc": 1790000100,
                                        "body": "Yes, signal failure, trains suspended since 8am", "parent_id": "t3_a1", "link_id": "t3_a1",
                                        "replies": _listing([{"kind": "t1", "data": {"id": "c2", "name": "t1_c2", "author": "fourth",
                                                              "created_utc": 1790000200, "body": "lol", "parent_id": "t1_c1", "link_id": "t3_a1"}}])}}])])
        return _Resp({}, 404)


def test_capture_keeps_civic_items_and_drops_authors(tmp_path):
    from scraper.capture.sources.reddit import RedditSource
    st = Store(tmp_path)
    ctx = Context(store=st, state=State(st.state_dir, "reddit"), config={},
                  env={"REDDIT_CLIENT_ID": "id", "REDDIT_CLIENT_SECRET": "secret", "PSEUDONYM_SALT": "s"}, session=FakeSession())
    src = RedditSource({"subreddits": ["NewcastleUponTyne"], "backfill_pages": 1})
    import scraper.capture.sources.reddit as R
    R.RedditClient.pause = 0
    payloads = src.fetch(ctx)
    assert len(payloads) == 1
    d = json.loads(payloads[0].body)
    kinds = [(e["kind"], e["item"]["id"]) for e in d["items"]]
    assert kinds == [("submission", "a1"), ("comment", "c1")]          # the pizza post and 'lol' are not stored
    for e in d["items"]:
        assert "author" not in e["item"] and "author_fullname" not in e["item"] and e["author_pid"]
    assert ctx.state.get("last_created_NewcastleUponTyne") == 1790000000


def test_adapter_records():
    from scraper.feeds.sources.reddit import RedditAdapter
    body = json.dumps({"subreddit": "NewcastleUponTyne", "items": [
        {"kind": "comment", "item": {"name": "t1_c1", "body": "Yes, signal failure, trains suspended since 8am",
                                     "created_utc": 1790000100, "link_id": "t3_a1"},
         "analysis": {"topic_hits": {"transport": ["trains"]}, "place_hits": {"strong": [], "generic": []}}, "author_pid": "p"}]}).encode()
    recs = RedditAdapter().records(body, {"source": "reddit", "file": "x", "fetched_at": "2026-09-28T10:00:00Z"})
    assert len(recs) == 1
    r = recs[0]
    assert r.domain == "transport" and r.native_id == "t1_c1" and r.place.name == "r/NewcastleUponTyne"
    assert r.structured["item_kind"] == "comment" and r.structured["author_pid"] == "p"

"""Reddit collection through the official Data API (author's choice 2026-09-28, in place of Bluesky claims and of any
scraping of X): application-only OAuth (client credentials; no user account, no password), public posts only.

Per configured local subreddit: the newest submissions (walking back with `after` to the last one already captured;
`backfill_pages` on the first run), and, for civic-relevant submissions, their comment threads, since first-hand
claims ("Metro's not running past Haymarket again") are often replies. Only items with a civic topic hit are
stored, and author fields are dropped at capture: an item keeps `author_pid` (HMAC of the username) only.

Env: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET (a 'script' or 'web' app at reddit.com/prefs/apps), PSEUDONYM_SALT, and
optionally REDDIT_USER_AGENT ('<platform>:<app id>:<version> (by /u/<username>)', as Reddit's API rules ask).
Rate limit: Reddit allows 100 requests/minute per client; a run stays far below it and sleeps between calls.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

from ..base import CaptureSource, Context
from ..store import Payload, utcnow
from .bluesky import Matcher, load_lexicon

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"
DEFAULT_UA = "python:neet-capture:0.1 (Newcastle University research)"
# Fields kept from a submission or comment; everything identifying the author is dropped here.
KEEP = ("id", "name", "subreddit", "created_utc", "title", "selftext", "body", "permalink", "link_id", "parent_id",
        "score", "num_comments", "over_18", "url", "link_flair_text", "is_self", "edited", "stickied")


def sanitise(item: dict[str, Any]) -> dict[str, Any]:
    return {k: item[k] for k in KEEP if k in item}


class RedditClient:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.ua = ctx.env.get("REDDIT_USER_AGENT") or DEFAULT_UA
        self.token: str | None = None
        self.calls = 0
        self.pause = 0.7                       # ~85 requests/minute at most

    def login(self) -> None:
        r = self.ctx.session.post(TOKEN_URL, auth=(self.ctx.env["REDDIT_CLIENT_ID"], self.ctx.env["REDDIT_CLIENT_SECRET"]),
                                  data={"grant_type": "client_credentials"}, headers={"User-Agent": self.ua}, timeout=30)
        r.raise_for_status()
        self.token = r.json()["access_token"]

    def get(self, path: str, **params: Any) -> Any:
        if not self.token:
            self.login()
        params = {"raw_json": 1, **{k: v for k, v in params.items() if v is not None}}
        for attempt in (1, 2):
            r = self.ctx.session.get(f"{API}{path}", params=params, timeout=60,
                                     headers={"Authorization": f"bearer {self.token}", "User-Agent": self.ua})
            self.calls += 1
            time.sleep(self.pause)
            if r.status_code == 401 and attempt == 1:
                self.login()
                continue
            r.raise_for_status()
            return r.json()


def _children(listing: Any) -> list[dict]:
    return [c.get("data") or {} for c in ((listing or {}).get("data") or {}).get("children") or [] if isinstance(c, dict)]


def _walk_comments(listing: Any, out: list[dict]) -> None:
    for c in ((listing or {}).get("data") or {}).get("children") or []:
        if not isinstance(c, dict) or c.get("kind") != "t1":
            continue
        d = c.get("data") or {}
        out.append(d)
        if isinstance(d.get("replies"), dict):
            _walk_comments(d["replies"], out)


class RedditSource(CaptureSource):
    name = "reddit"
    cadence_s = 3600
    requires_env = ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "PSEUDONYM_SALT")

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        super().__init__(cfg)
        self.subreddits: list[str] = list(self.cfg.get("subreddits") or [])
        self.max_pages = int(self.cfg.get("max_pages", 2))
        self.backfill_pages = int(self.cfg.get("backfill_pages", 10))
        self.max_threads = int(self.cfg.get("max_comment_threads", 25))
        self.matcher = Matcher(load_lexicon(Path(self.cfg["lexicon"]) if self.cfg.get("lexicon") else None))

    def _pid(self, ctx: Context, author: str | None) -> str | None:
        if not author or author == "[deleted]":
            return None
        return hmac.new(ctx.env["PSEUDONYM_SALT"].encode(), f"reddit:{author}".encode(), hashlib.sha256).hexdigest()[:24]

    def _analyse(self, text: str) -> dict[str, Any]:
        # the Matcher reads Bluesky-shaped posts; give it the text the same way
        return self.matcher.analyse({"record": {"text": text}})

    def _entry(self, ctx: Context, kind: str, item: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
        return {"kind": kind, "item": sanitise(item), "analysis": analysis, "author_pid": self._pid(ctx, item.get("author")),
                "captured_at": utcnow().isoformat()}

    def fetch(self, ctx: Context) -> list[Payload]:
        client = RedditClient(ctx)
        client.login()
        out: list[Payload] = []
        threads_left = self.max_threads
        for sub in self.subreddits:
            last = float(ctx.state.get(f"last_created_{sub}", 0) or 0)
            pages = self.max_pages if last else self.backfill_pages
            submissions, after, newest = [], None, last
            try:
                for _ in range(pages):
                    listing = client.get(f"/r/{sub}/new", limit=100, after=after)
                    batch = _children(listing)
                    if not batch:
                        break
                    fresh = [s for s in batch if float(s.get("created_utc") or 0) > last]
                    submissions += fresh
                    newest = max([newest] + [float(s.get("created_utc") or 0) for s in batch])
                    after = ((listing or {}).get("data") or {}).get("after")
                    if len(fresh) < len(batch) or not after:
                        break
            except Exception as exc:                      # a missing or private subreddit: record and go on
                ctx.store.health({"source": self.name, "subreddit": sub, "error": str(exc)[:200]})
                continue
            entries, seen = [], 0
            for s in submissions:
                seen += 1
                text = "\n".join(t for t in (s.get("title"), s.get("selftext")) if t)
                a = self._analyse(text)
                if not a["topic_hits"]:
                    continue
                entries.append(self._entry(ctx, "submission", s, a))
                if int(s.get("num_comments") or 0) and threads_left > 0:
                    threads_left -= 1
                    try:
                        thread = client.get(f"/r/{sub}/comments/{s.get('id')}", limit=200, depth=3)
                    except Exception as exc:
                        ctx.store.health({"source": self.name, "subreddit": sub, "thread": s.get("id"), "error": str(exc)[:200]})
                        continue
                    comments: list[dict] = []
                    if isinstance(thread, list) and len(thread) > 1:
                        _walk_comments(thread[1], comments)
                    for c in comments:
                        seen += 1
                        ca = self._analyse(c.get("body") or "")
                        if ca["topic_hits"]:
                            entries.append(self._entry(ctx, "comment", c, ca))
            ctx.state.set(f"last_created_{sub}", newest)
            if entries:
                body = json.dumps({"subreddit": sub, "items": entries}, ensure_ascii=False, sort_keys=True).encode("utf-8")
                out.append(Payload(tag=f"new_{sub}", body=body, ext="json",
                                   meta={"subreddit": sub, "seen": seen, "kept": len(entries), "calls": client.calls},
                                   n_items=len(entries)))
        return out

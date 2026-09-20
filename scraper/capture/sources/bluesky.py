"""Bluesky collection, five tagged strategies (see docs/bluesky_strategies in the Civic-Verification folder):

S1-KW   place-term search queries (Lucene-ish: quoted phrase + -exclusions), 30-min windows, client-side topic filter
S2-AUTH seed accounts' posts + posts mentioning them
S3-PROF "local" authors (bio terms + starter-pack members) -> their posts (daily), client-side topic filter
S4-FEED community feed generators
S5-HASH hashtag searches

Constraints verified 2026-09-20: searchPosts has no OR; cursor unreliable -> time windows; unauthenticated AppView
403s intermittently -> authenticated session on the user's PDS (BSKY_HANDLE + BSKY_APP_PASSWORD).
Every stored post gets `strategies` tags, `topic_hits`, `place_hits` and a pseudonymous author id (HMAC of the DID).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from ..base import CaptureSource, Context
from ..store import Payload, utcnow

LEXICON_PATH = Path(__file__).parent.parent / "lexicon" / "newcastle.yaml"
PDS_ENTRY = "https://bsky.social"


def load_lexicon(path: Path | None = None) -> dict[str, Any]:
    with (path or LEXICON_PATH).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _quote(term: str) -> str:
    return term if term.startswith('"') else (f'"{term}"' if " " in term or "'" in term else term)


def build_s1_queries(lex: dict[str, Any]) -> list[tuple[str, str]]:
    """(place, query) pairs: tier1 + tier2 places, each with the exclusion suffix. Tier3 generic names are not
    queried alone; they only count as place cues client-side when a tier1/2 term or a topic term co-occurs."""
    exclusions = " ".join(f"-{_quote(e)}" for e in lex.get("exclusions", []))
    generic = set(lex["places"].get("tier3_generic_needs_cue", []))
    places = [p for p in dict.fromkeys(lex["places"]["tier1"] + lex["places"]["tier2_metro_stations"]) if p not in generic]
    return [(p, f"{_quote(p)} {exclusions}".strip()) for p in places]


def compile_terms(terms: Iterable[str]) -> list[tuple[str, re.Pattern]]:
    out = []
    for t in terms:
        t2 = t.strip().strip('"')
        if not t2:
            continue
        out.append((t2, re.compile(r"(?<![A-Za-z0-9])" + re.escape(t2) + r"(?![A-Za-z0-9])", re.I)))
    return out


class Matcher:
    def __init__(self, lex: dict[str, Any]) -> None:
        self.topics = {k: compile_terms(v) for k, v in lex.get("topics", {}).items()}
        pl = lex["places"]
        generic = set(pl.get("tier3_generic_needs_cue", []))
        self.places_strong = compile_terms([p for p in pl["tier1"] + pl["tier2_metro_stations"] if p not in generic])
        self.places_generic = compile_terms(pl.get("tier3_generic_needs_cue", []))
        self.exclusions = compile_terms(lex.get("exclusions", []))

    @staticmethod
    def post_text(post: dict[str, Any]) -> str:
        rec = post.get("record", {}) or {}
        parts = [rec.get("text", "")]
        emb = post.get("embed") or {}
        ext = emb.get("external") or (emb.get("media") or {}).get("external")
        if ext:
            parts += [ext.get("title", ""), ext.get("description", "")]
        for img in (emb.get("images") or []) + ((emb.get("media") or {}).get("images") or []):
            parts.append(img.get("alt", ""))
        return "\n".join(p for p in parts if p)

    def analyse(self, post: dict[str, Any]) -> dict[str, Any]:
        text = self.post_text(post)
        topic_hits = {k: [t for t, rx in v if rx.search(text)] for k, v in self.topics.items()}
        topic_hits = {k: v for k, v in topic_hits.items() if v}
        strong = [t for t, rx in self.places_strong if rx.search(text)]
        generic = [t for t, rx in self.places_generic if rx.search(text)]
        excl = [t for t, rx in self.exclusions if rx.search(text)]
        place_ok = bool(strong) or (bool(generic) and bool(topic_hits))
        return {"topic_hits": topic_hits, "place_hits": {"strong": strong, "generic": generic},
                "exclusion_hits": excl, "place_ok": place_ok, "text_len": len(text)}


class BlueskyClient:
    def __init__(self, ctx: Context, handle: str, app_password: str) -> None:
        self.ctx = ctx
        self.handle = handle
        self.app_password = app_password
        self.pds = ctx.state.get("pds", PDS_ENTRY)
        self.jwt: str | None = None
        self.did: str | None = None
        self.calls = 0

    def login(self) -> None:
        r = self.ctx.session.post(f"{PDS_ENTRY}/xrpc/com.atproto.server.createSession",
                                  json={"identifier": self.handle, "password": self.app_password}, timeout=30)
        r.raise_for_status()
        d = r.json()
        self.jwt, self.did = d["accessJwt"], d["did"]
        svc = (d.get("didDoc") or {}).get("service") or []
        for s in svc:
            if s.get("type") == "AtprotoPersonalDataServer" and s.get("serviceEndpoint"):
                self.pds = s["serviceEndpoint"]
        self.ctx.state.set("pds", self.pds)

    def xrpc(self, method: str, **params: Any) -> dict[str, Any]:
        if not self.jwt:
            self.login()
        params = {k: v for k, v in params.items() if v is not None}
        r = self.ctx.session.get(f"{self.pds}/xrpc/{method}", params=params,
                                 headers={"Authorization": f"Bearer {self.jwt}", "User-Agent": "NEET-capture/0.1 (research)"}, timeout=60)
        self.calls += 1
        if r.status_code == 401:
            self.login()
            r = self.ctx.session.get(f"{self.pds}/xrpc/{method}", params=params,
                                     headers={"Authorization": f"Bearer {self.jwt}"}, timeout=60)
        r.raise_for_status()
        return r.json()

    # convenience wrappers
    def search_posts(self, q: str, since: str | None = None, until: str | None = None, tag: str | None = None,
                     mentions: str | None = None, author: str | None = None, limit: int = 100, cursor: str | None = None) -> dict:
        return self.xrpc("app.bsky.feed.searchPosts", q=q, since=since, until=until, tag=tag, mentions=mentions,
                         author=author, limit=limit, cursor=cursor, lang="en", sort="latest")

    def author_feed(self, actor: str, limit: int = 100, cursor: str | None = None) -> dict:
        return self.xrpc("app.bsky.feed.getAuthorFeed", actor=actor, limit=limit, cursor=cursor, filter="posts_with_replies")

    def feed(self, feed_uri: str, limit: int = 100, cursor: str | None = None) -> dict:
        return self.xrpc("app.bsky.feed.getFeed", feed=feed_uri, limit=limit, cursor=cursor)

    def search_actors(self, q: str, limit: int = 100, cursor: str | None = None) -> dict:
        return self.xrpc("app.bsky.actor.searchActors", q=q, limit=limit, cursor=cursor)

    def starter_pack(self, uri: str) -> dict:
        return self.xrpc("app.bsky.graph.getStarterPack", starterPack=uri)

    def list_members(self, list_uri: str, limit: int = 100, cursor: str | None = None) -> dict:
        return self.xrpc("app.bsky.graph.getList", list=list_uri, limit=limit, cursor=cursor)


class BlueskySource(CaptureSource):
    name = "bluesky"
    cadence_s = 1800
    requires_env = ("BSKY_HANDLE", "BSKY_APP_PASSWORD", "PSEUDONYM_SALT")

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        super().__init__(cfg)
        self.lex = load_lexicon(Path(self.cfg["lexicon"]) if self.cfg.get("lexicon") else None)
        self.matcher = Matcher(self.lex)
        self._run_seen: dict[str, dict] = {}

    # -- helpers ---------------------------------------------------------------
    def _pid(self, ctx: Context, did: str) -> str:
        return hmac.new(ctx.env["PSEUDONYM_SALT"].encode(), did.encode(), hashlib.sha256).hexdigest()[:24]

    def _keep(self, analysis: dict[str, Any], author_local: bool) -> bool:
        """Store only civic-relevant or locally-authored posts (limits personal-data collection)."""
        return bool(analysis["topic_hits"]) or author_local

    def _emit(self, ctx: Context, tag: str, strategy: str, posts: list[dict], local_dids: set[str], extra: dict | None = None,
              require_place: bool = False, keep_all: bool = False) -> tuple[Payload | None, dict]:
        """Filter posts and build one payload. Posts already kept earlier in this run (same URI) are not stored
        again: their existing record gains this strategy/query in `strategies`/`matched_by` and the payload only
        lists their URIs under `dupes` (so per-strategy recall can still be computed from the payloads)."""
        kept, seen, dupes = [], 0, []
        for p in posts:
            seen += 1
            uri = p.get("uri")
            a = self.matcher.analyse(p)
            did = (p.get("author") or {}).get("did", "")
            local = did in local_dids
            if require_place and not a["place_ok"]:
                continue
            if not keep_all and not self._keep(a, local):
                continue
            match = {"strategy": strategy, "tag": tag, **{k: v for k, v in (extra or {}).items() if k in ("q", "tag", "feed", "actor", "mentions", "actor_pid")}}
            if uri and uri in self._run_seen:
                rec = self._run_seen[uri]
                if strategy not in rec["strategies"]:
                    rec["strategies"].append(strategy)
                rec["matched_by"].append(match)
                dupes.append(uri)
                continue
            rec = {"post": p, "analysis": a, "author_pid": self._pid(ctx, did) if did else None,
                   "author_local": local, "strategies": [strategy], "matched_by": [match], "captured_at": utcnow().isoformat()}
            if uri:
                self._run_seen[uri] = rec
            kept.append(rec)
        stats = {"seen": seen, "kept": len(kept), "dupes": len(dupes)}
        if not kept and not dupes:
            return None, stats
        pl = Payload(tag=tag, body=b"", ext="json", meta={"strategy": strategy, **(extra or {}), **stats}, n_items=len(kept))
        self._pending.append((pl, {"strategy": strategy, "query": extra or {}, "posts": kept, "dupes": dupes}))
        return pl, stats

    # -- strategies ------------------------------------------------------------
    def fetch(self, ctx: Context) -> list[Payload]:
        client = BlueskyClient(ctx, ctx.env["BSKY_HANDLE"], ctx.env["BSKY_APP_PASSWORD"])
        client.login()
        self._run_seen: dict[str, dict] = {}
        self._pending: list[tuple[Payload, dict]] = []
        out: list[Payload] = []
        stats: dict[str, dict] = {}
        now = utcnow().replace(microsecond=0)
        local_dids = set(ctx.state.get("local_dids", []))
        seed_dids = set(ctx.state.get("seed_dids", {}).values())

        # S2/S3 author sets refresh (daily / weekly)
        if self._due(ctx, "seeds_at", days=1):
            out.append(self._refresh_seeds(ctx, client))
        if self._due(ctx, "locals_at", days=7):
            out.append(self._refresh_locals(ctx, client))
            local_dids = set(ctx.state.get("local_dids", []))
        all_local = local_dids | seed_dids

        # S1-KW: windowed place searches
        since_iso = ctx.state.get("s1_until") or (now - timedelta(minutes=45)).isoformat().replace("+00:00", "Z")
        until_iso = now.isoformat().replace("+00:00", "Z")
        s1 = {"seen": 0, "kept": 0, "queries": 0, "errors": 0}
        for place, q in build_s1_queries(self.lex):
            try:
                d = client.search_posts(q, since=since_iso, until=until_iso)
            except Exception as exc:  # keep going; record error
                s1["errors"] += 1
                ctx.store.health({"source": self.name, "strategy": "S1-KW", "place": place, "error": str(exc)[:200]})
                continue
            s1["queries"] += 1
            pl, st = self._emit(ctx, f"S1-KW_{place}", "S1-KW", d.get("posts", []), all_local,
                                extra={"q": q, "since": since_iso, "until": until_iso, "hitsTotal": d.get("hitsTotal")}, require_place=True)
            s1["seen"] += st["seen"]; s1["kept"] += st["kept"]
            if pl:
                out.append(pl)
        ctx.state.set("s1_until", until_iso)
        stats["S1-KW"] = s1

        # S2-AUTH: seed accounts' own posts + mentions
        s2 = {"seen": 0, "kept": 0, "errors": 0}
        cursors = ctx.state.get("s2_cursors", {})
        for handle, did in ctx.state.get("seed_dids", {}).items():
            try:
                d = client.author_feed(did, limit=100)
                posts = [it["post"] for it in d.get("feed", []) if it.get("post")]
                last_seen = cursors.get(handle)
                new = [p for p in posts if not last_seen or p.get("indexedAt", "") > last_seen]
                if posts:
                    cursors[handle] = max(p.get("indexedAt", "") for p in posts)
                pl, st = self._emit(ctx, f"S2-AUTH_{handle}", "S2-AUTH", new, all_local, extra={"actor": handle}, keep_all=True)
                s2["seen"] += st["seen"]; s2["kept"] += st["kept"]
                if pl:
                    out.append(pl)
                dm = client.search_posts("*", mentions=handle, since=since_iso, until=until_iso)
                pl, st = self._emit(ctx, f"S2-MENTION_{handle}", "S2-AUTH", dm.get("posts", []), all_local,
                                    extra={"mentions": handle, "since": since_iso, "until": until_iso}, keep_all=True)
                s2["seen"] += st["seen"]; s2["kept"] += st["kept"]
                if pl:
                    out.append(pl)
            except Exception as exc:
                s2["errors"] += 1
                ctx.store.health({"source": self.name, "strategy": "S2-AUTH", "actor": handle, "error": str(exc)[:200]})
        ctx.state.set("s2_cursors", cursors)
        stats["S2-AUTH"] = s2

        # S3-PROF: local authors' recent posts (daily), topic-filtered
        if self._due(ctx, "s3_at", days=1):
            s3 = {"seen": 0, "kept": 0, "errors": 0, "authors": 0}
            cur3 = ctx.state.get("s3_cursors", {})
            for did in sorted(local_dids)[: self.cfg.get("s3_max_authors", 600)]:
                try:
                    d = client.author_feed(did, limit=50)
                    posts = [it["post"] for it in d.get("feed", []) if it.get("post")]
                    last_seen = cur3.get(did)
                    new = [p for p in posts if not last_seen or p.get("indexedAt", "") > last_seen]
                    if posts:
                        cur3[did] = max(p.get("indexedAt", "") for p in posts)
                    pl, st = self._emit(ctx, f"S3-PROF_{did[-8:]}", "S3-PROF", new, all_local, extra={"actor_pid": self._pid(ctx, did)})
                    s3["seen"] += st["seen"]; s3["kept"] += st["kept"]; s3["authors"] += 1
                    if pl:
                        out.append(pl)
                except Exception as exc:
                    s3["errors"] += 1
            ctx.state.set("s3_cursors", cur3)
            ctx.state.set("s3_at", now.isoformat())
            stats["S3-PROF"] = s3

        # S4-FEED
        s4 = {"seen": 0, "kept": 0, "errors": 0}
        cur4 = ctx.state.get("s4_cursors", {})
        for uri in self.lex.get("feeds", []):
            try:
                d = client.feed(uri, limit=100)
                posts = [it["post"] for it in d.get("feed", []) if it.get("post")]
                last_seen = cur4.get(uri)
                new = [p for p in posts if not last_seen or p.get("indexedAt", "") > last_seen]
                if posts:
                    cur4[uri] = max(p.get("indexedAt", "") for p in posts)
                pl, st = self._emit(ctx, f"S4-FEED_{uri.rsplit('/', 1)[-1]}", "S4-FEED", new, all_local, extra={"feed": uri})
                s4["seen"] += st["seen"]; s4["kept"] += st["kept"]
                if pl:
                    out.append(pl)
            except Exception as exc:
                s4["errors"] += 1
                ctx.store.health({"source": self.name, "strategy": "S4-FEED", "feed": uri, "error": str(exc)[:200]})
        ctx.state.set("s4_cursors", cur4)
        stats["S4-FEED"] = s4

        # S5-HASH
        s5 = {"seen": 0, "kept": 0, "errors": 0}
        for h in self.lex.get("hashtags", []):
            try:
                d = client.search_posts("*", tag=h, since=since_iso, until=until_iso)
                pl, st = self._emit(ctx, f"S5-HASH_{h}", "S5-HASH", d.get("posts", []), all_local,
                                    extra={"tag": h, "since": since_iso, "until": until_iso}, require_place=False)
                s5["seen"] += st["seen"]; s5["kept"] += st["kept"]
                if pl:
                    out.append(pl)
            except Exception as exc:
                s5["errors"] += 1
        stats["S5-HASH"] = s5

        ctx.store.health({"source": self.name, "event": "strategy_stats", "stats": stats, "api_calls": client.calls})
        # serialise now, after all strategies have merged their matches into shared records
        for pl, doc in self._pending:
            pl.body = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        return [p for p in out if p is not None]

    def _due(self, ctx: Context, key: str, days: int) -> bool:
        v = ctx.state.get(key)
        return not v or (utcnow() - datetime.fromisoformat(v)) > timedelta(days=days)

    def _refresh_seeds(self, ctx: Context, client: BlueskyClient) -> Payload | None:
        handles = [h for grp in self.lex.get("seed_accounts", {}).values() for h in grp]
        dids: dict[str, str] = dict(ctx.state.get("seed_dids", {}))
        profiles = []
        for h in handles:
            try:
                d = client.xrpc("app.bsky.actor.getProfile", actor=h)
                dids[h] = d["did"]
                profiles.append(d)
            except Exception as exc:
                ctx.store.health({"source": self.name, "event": "seed_resolve_error", "handle": h, "error": str(exc)[:200]})
        ctx.state.set("seed_dids", dids)
        ctx.state.set("seeds_at", utcnow().isoformat())
        body = json.dumps({"profiles": profiles}, ensure_ascii=False).encode("utf-8")
        return Payload(tag="S2-AUTH_seed_profiles", body=body, ext="json", meta={"strategy": "S2-AUTH"}, n_items=len(profiles))

    def _refresh_locals(self, ctx: Context, client: BlueskyClient) -> Payload | None:
        found: dict[str, dict] = {}
        for term in self.lex.get("bio_terms", []):
            try:
                cursor = None
                for _ in range(3):
                    d = client.search_actors(term, limit=100, cursor=cursor)
                    for a in d.get("actors", []):
                        desc = (a.get("description") or "")
                        if re.search(r"(?<![A-Za-z0-9])" + re.escape(term.strip('"')) + r"(?![A-Za-z0-9])", desc, re.I):
                            found.setdefault(a["did"], {"did": a["did"], "handle": a.get("handle"), "via": []})["via"].append(f"bio:{term}")
                    cursor = d.get("cursor")
                    if not cursor:
                        break
            except Exception as exc:
                ctx.store.health({"source": self.name, "event": "bio_search_error", "term": term, "error": str(exc)[:200]})
        for sp in self.lex.get("starter_packs", []):
            try:
                d = client.starter_pack(sp)
                list_uri = ((d.get("starterPack") or {}).get("list") or {}).get("uri")
                cursor = None
                while list_uri:
                    m = client.list_members(list_uri, limit=100, cursor=cursor)
                    for it in m.get("items", []):
                        s = it.get("subject") or {}
                        if s.get("did"):
                            found.setdefault(s["did"], {"did": s["did"], "handle": s.get("handle"), "via": []})["via"].append(f"pack:{sp.rsplit('/', 1)[-1]}")
                    cursor = m.get("cursor")
                    if not cursor:
                        break
            except Exception as exc:
                ctx.store.health({"source": self.name, "event": "starter_pack_error", "uri": sp, "error": str(exc)[:200]})
        ctx.state.set("local_dids", sorted(found))
        ctx.state.set("locals_at", utcnow().isoformat())
        # store the mapping privately (pseudonym -> via reasons; DIDs kept because the private copy is full-fidelity)
        body = json.dumps({"locals": list(found.values())}, ensure_ascii=False).encode("utf-8")
        return Payload(tag="S3-PROF_local_authors", body=body, ext="json", meta={"strategy": "S3-PROF"}, n_items=len(found))

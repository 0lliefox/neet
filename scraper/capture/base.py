"""Base class for capture sources."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

from .state import State
from .store import Payload, Store

logger = logging.getLogger(__name__)

USER_AGENT = "NEET-capture/0.1 (Newcastle University research; contact via repo)"


@dataclass
class Context:
    store: Store
    state: State
    config: dict[str, Any]       # the `capture:` section of config.yaml (+ source sub-config)
    env: dict[str, str]          # os.environ snapshot
    session: requests.Session


class CaptureSource:
    """Subclass and implement ``fetch``. One instance = one source name."""

    name: str = "base"
    cadence_s: int = 3600
    requires_env: tuple[str, ...] = ()   # env vars that must be present, else the source is skipped

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        self.cfg = cfg or {}
        self.cadence_s = int(self.cfg.get("cadence_s", self.cadence_s))

    def missing_env(self, env: dict[str, str]) -> list[str]:
        return [k for k in self.requires_env if not env.get(k)]

    def fetch(self, ctx: Context) -> list[Payload]:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def get(ctx: Context, url: str, *, params: dict | None = None, headers: dict | None = None,
            timeout: int = 60) -> requests.Response:
        h = {"User-Agent": USER_AGENT}
        if headers:
            h.update(headers)
        r = ctx.session.get(url, params=params, headers=h, timeout=timeout)
        return r

    @staticmethod
    def payload_from_response(tag: str, r: requests.Response, ext: str | None = None,
                              extra_meta: dict | None = None, n_items: int | None = None) -> Payload:
        ct = r.headers.get("Content-Type", "")
        if ext is None:
            ext = "json" if "json" in ct else ("xml" if ("xml" in ct or "atom" in ct or "rss" in ct) else "txt")
        meta = {"url": r.url, "status": r.status_code, "content_type": ct}
        if extra_meta:
            meta.update(extra_meta)
        return Payload(tag=tag, body=r.content, ext=ext, meta=meta, n_items=n_items)


def env_snapshot() -> dict[str, str]:
    return {k: v for k, v in os.environ.items()}

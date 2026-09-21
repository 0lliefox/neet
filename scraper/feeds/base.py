"""Adapter contract: raw capture payload (bytes + meta.json) -> list[EvidenceRecord]. Pure functions, no I/O.

Adapters never assume "no file = no data": the store skips unchanged payloads by content hash, so a record's
validity is carried forward by `build.py` from the last *written* capture of the same (source, tag).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

from .model import EvidenceRecord, Place

_REGISTRY: dict[str, "Adapter"] = {}


def register(adapter: "Adapter") -> "Adapter":
    _REGISTRY[adapter.source] = adapter
    return adapter


def adapters() -> dict[str, "Adapter"]:
    # importing the package registers every adapter module
    from . import sources  # noqa: F401
    return dict(_REGISTRY)


class Adapter:
    """One per capture source. Subclasses implement `records(body, meta)`.
    `accepts(meta)` may narrow to particular tags (e.g. only `updates`, not the raw `page`)."""
    source: str = "base"
    reliability_hint: str = "official_operator"

    def accepts(self, meta: dict[str, Any]) -> bool:
        return True

    def records(self, body: bytes, meta: dict[str, Any]) -> list[EvidenceRecord]:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- helpers shared by adapters -------------------------------------------------
    def provenance(self, meta: dict[str, Any]) -> dict[str, Any]:
        return {k: meta.get(k) for k in ("file", "sha256", "url", "tag", "source") if meta.get(k) is not None}

    def fetched_at(self, meta: dict[str, Any]) -> Optional[str]:
        return iso_utc(meta.get("fetched_at"))


# -- time helpers -----------------------------------------------------------------------
_ISO_Z = re.compile(r"Z$")


def iso_utc(value: Any) -> Optional[str]:
    """Normalise an ISO-8601 string / epoch (s or ms) / datetime to 'YYYY-MM-DDTHH:MM:SSZ' (UTC)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e11:  # epoch milliseconds
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(_ISO_Z.sub("+00:00", s))
    except ValueError:
        try:
            dt = datetime.strptime(s, "%a, %d %b %Y %H:%M:%S %z")   # RFC 822 (RSS pubDate)
        except ValueError:
            try:
                dt = datetime.strptime(s, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def month_bounds(yyyy_mm: str) -> tuple[Optional[str], Optional[str]]:
    """'2026-05' -> ('2026-05-01T00:00:00Z', '2026-05-31T23:59:59Z')."""
    try:
        y, m = (int(x) for x in yyyy_mm.split("-")[:2])
    except (ValueError, AttributeError):
        return None, None
    import calendar
    last = calendar.monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-01T00:00:00Z", f"{y:04d}-{m:02d}-{last:02d}T23:59:59Z"


def load_json(body: bytes) -> Any:
    return json.loads(body.decode("utf-8"))


def first(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default

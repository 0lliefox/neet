"""Immutable raw-payload store + health log.

Layout under ``data_root``::

    capture/<source>/<YYYY-MM-DD>/<HHMMSS>_<tag>.<ext>      raw payload
    capture/<source>/<YYYY-MM-DD>/<HHMMSS>_<tag>.meta.json  fetch metadata
    state/<source>.json                                     per-source cursors (see state.py)
    logs/health.jsonl                                       one line per source run
    logs/capture.log                                        text log

Writes are atomic (tmp file + rename) because ``data_root`` may be a cloud-drive mount.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe(s: str, limit: int = 80) -> str:
    return _SAFE.sub("_", s).strip("_")[:limit] or "x"


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8"))


@dataclass
class Payload:
    """One raw fetch result to persist."""
    tag: str                      # e.g. "floods_tyne", "S1-KW_flood_Jesmond"
    body: bytes                   # raw bytes exactly as received (or canonical JSON if synthesised)
    ext: str = "json"             # file extension
    meta: dict[str, Any] = field(default_factory=dict)  # url, params, status, strategy tags, ...
    n_items: int | None = None    # optional item count for the health log


class Store:
    def __init__(self, data_root: str | Path) -> None:
        self.root = Path(data_root).expanduser()
        self.capture_dir = self.root / "capture"
        self.state_dir = self.root / "state"
        self.logs_dir = self.root / "logs"
        for d in (self.capture_dir, self.state_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    # -- payloads -----------------------------------------------------------
    def write(self, source: str, payload: Payload, when: datetime | None = None) -> Path:
        when = when or utcnow()
        day = when.strftime("%Y-%m-%d")
        stamp = when.strftime("%H%M%S")
        base = f"{stamp}_{_safe(payload.tag)}"
        d = self.capture_dir / _safe(source) / day
        path = d / f"{base}.{payload.ext}"
        i = 1
        while path.exists():  # same second, same tag
            i += 1
            path = d / f"{base}_{i}.{payload.ext}"
        atomic_write_bytes(path, payload.body)
        meta = dict(payload.meta)
        meta.update({
            "source": source,
            "tag": payload.tag,
            "fetched_at": when.isoformat(),
            "bytes": len(payload.body),
            "sha256": hashlib.sha256(payload.body).hexdigest(),
            "n_items": payload.n_items,
            "file": path.name,
        })
        atomic_write_json(path.with_suffix(path.suffix + ".meta.json") if not path.name.endswith(".meta.json") else path, meta)
        return path

    # -- health --------------------------------------------------------------
    def health(self, record: dict[str, Any]) -> None:
        record = {"at": utcnow().isoformat(), **record}
        line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        with (self.logs_dir / "health.jsonl").open("ab") as f:
            f.write(line)

    def last_health(self) -> dict[str, dict[str, Any]]:
        """Latest health record per source (reads the whole file; fine at our volumes)."""
        out: dict[str, dict[str, Any]] = {}
        p = self.logs_dir / "health.jsonl"
        if not p.exists():
            return out
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                out[r.get("source", "?")] = r
        return out

"""Per-source persistent state (cursors, last-run times)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .store import atomic_write_json


class State:
    def __init__(self, state_dir: Path, source: str) -> None:
        self.path = state_dir / f"{source}.json"
        self.data: dict[str, Any] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.data = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def save(self) -> None:
        atomic_write_json(self.path, self.data)

    # -- scheduling helpers ---------------------------------------------------
    def last_run(self) -> datetime | None:
        v = self.data.get("last_run")
        return datetime.fromisoformat(v) if v else None

    def due(self, cadence_s: int, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        lr = self.last_run()
        return lr is None or (now - lr) >= timedelta(seconds=cadence_s)

    def mark_run(self, now: datetime | None = None, ok: bool = True) -> None:
        now = now or datetime.now(timezone.utc)
        self.data["last_run"] = now.isoformat()
        if ok:
            self.data["last_success"] = now.isoformat()

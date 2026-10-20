"""Per-source persistent state (cursors, last-run times)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .store import atomic_write_json

# First retry after a failed run. Slightly under the 600 s launchd tick so it lands on the next tick
# (last_run is stamped at the end of a fetch, after the tick started).
RETRY_BASE_S = 540


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

    def last_success(self) -> datetime | None:
        v = self.data.get("last_success")
        return datetime.fromisoformat(v) if v else None

    def fail_streak(self) -> int:
        """Consecutive failed runs. State files written before the streak was kept count as 1 if the last run failed."""
        streak = int(self.data.get("fail_streak", 0) or 0)
        lr, ls = self.last_run(), self.last_success()
        if not streak and lr is not None and (ls is None or ls < lr):
            streak = 1
        return streak

    def due(self, cadence_s: int, now: datetime | None = None) -> bool:
        """Due once a cadence has passed since the last run; after a failure, retry sooner (RETRY_BASE_S doubling
        per consecutive failure, never longer than the cadence), so one transient outage does not cost a daily
        source its whole day."""
        now = now or datetime.now(timezone.utc)
        lr = self.last_run()
        if lr is None:
            return True
        wait = cadence_s
        streak = self.fail_streak()
        if streak:
            wait = min(cadence_s, RETRY_BASE_S * 2 ** min(streak - 1, 16))
        return (now - lr) >= timedelta(seconds=wait)

    def mark_run(self, now: datetime | None = None, ok: bool = True) -> None:
        now = now or datetime.now(timezone.utc)
        streak = 0 if ok else self.fail_streak() + 1  # before last_run moves, or a legacy file would count twice
        self.data["last_run"] = now.isoformat()
        self.data["fail_streak"] = streak
        if ok:
            self.data["last_success"] = now.isoformat()

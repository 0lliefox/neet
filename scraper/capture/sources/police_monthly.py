"""Police.uk street-level crime around each configured point, once per month (data lags ~2 months)."""
from __future__ import annotations

import time
from datetime import timedelta

from datetime import datetime

from ..base import CaptureSource, Context
from ..store import Payload, utcnow


class PoliceMonthlySource(CaptureSource):
    name = "police"
    cadence_s = 24 * 3600

    def _get(self, ctx: Context, params: dict):
        # Police.uk rate-limits bursts (HTTP 429): pause between calls, and back off and retry a 429 a few times.
        # `pause_s` / `retry_s` come from config so tests can set them to 0.
        pause, retry = float(self.cfg.get("pause_s", 1.0)), float(self.cfg.get("retry_s", 10.0))
        for attempt in range(4):
            time.sleep(pause)
            r = self.get(ctx, "https://data.police.uk/api/crimes-street/all-crime", params=params, timeout=120)
            if r.status_code != 429:
                return r
            try:
                wait = float(r.headers.get("Retry-After", ""))
            except ValueError:
                wait = retry * (attempt + 1)
            time.sleep(min(wait, 60.0))
        return r

    def fetch(self, ctx: Context) -> list[Payload]:
        # Done months are tracked per point, so a point added later still backfills the months the others already
        # have, and one point's success never marks the month done for the rest. The old shared `months_done` key is
        # left in state untouched; the first run after the change re-fetches the last four months once, and the
        # store's hash de-dup drops the unchanged bodies.
        done: dict[str, list[str]] = ctx.state.get("months_done_by_point", {})
        recap_after, recap_points, recap_done = self._recapture(ctx)
        out = []
        now = utcnow()
        recapturing = recap_after is not None and now >= recap_after
        for back in range(1, 5):  # try the last 4 months; API returns 404 until a month is published
            m = (now.replace(day=1) - timedelta(days=28 * back)).strftime("%Y-%m")
            for point in self.cfg.get("points", [{"lat": 54.9781, "lng": -1.6162, "name": "newcastle_centre"}]):
                name = point["name"]
                recap = recapturing and name in recap_points and m not in recap_done.get(name, [])
                if m in done.get(name, []) and not recap:
                    continue
                r = self._get(ctx, {"lat": point["lat"], "lng": point["lng"], "date": m})
                if r.status_code == 404:
                    continue
                n = len(r.json()) if r.ok else None
                extra = {"recapture_after": recap_after.isoformat()} if recap else None
                p = self.payload_from_response(f"{name}_{m}", r, ext="json", n_items=n, extra_meta=extra)
                p.keep_duplicate = recap
                out.append(p)
                if r.ok and n:
                    done.setdefault(name, []).append(m)  # an empty list means not published yet: retry tomorrow
                    if recapturing and name in recap_points:
                        recap_done.setdefault(name, []).append(m)
        ctx.state.set("months_done_by_point", {k: sorted(set(v))[-24:] for k, v in done.items()})
        if recap_after is not None:
            ctx.state.set("recapture", {"after": recap_after.isoformat(),
                                        "done": {k: sorted(set(v))[-24:] for k, v in recap_done.items()}})
        return out

    def _recapture(self, ctx: Context) -> tuple[datetime | None, set[str], dict[str, list[str]]]:
        """`recapture: {after: <ISO UTC>, points: [...]}`: once `after` has passed, fetch each listed point's
        months again, once, and store them even if unchanged. Set `after` to the LaSSI v4.4 freeze tag: Durham
        crime counts if Police.uk serves the month after the tag, and this leaves a capture dated after it.
        A new `after` value starts a new round."""
        cfg = self.cfg.get("recapture") or {}
        if not cfg.get("after"):
            return None, set(), {}
        after = datetime.fromisoformat(str(cfg["after"]).replace("Z", "+00:00"))
        if after.tzinfo is None:
            raise ValueError("police.recapture.after must carry a UTC offset, e.g. 2026-10-25T12:00:00Z")
        st = ctx.state.get("recapture") or {}
        done = st.get("done", {}) if st.get("after") == after.isoformat() else {}
        return after, set(cfg.get("points") or []), done

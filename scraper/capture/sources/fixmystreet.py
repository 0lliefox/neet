"""FixMyStreet reports for the study area via the public Open311 endpoint (citizen claims, thin but categorised).

Endpoint (no key): https://www.fixmystreet.com/open311/v2/requests.json?jurisdiction_id=fixmystreet.com
&agency_responsible=<body id>&start_date=…&end_date=…  — one payload per body per run, window = the last
`lookback_days` (default 7) so late updates are re-captured; identical windows de-dup by content hash."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..base import CaptureSource, Context
from ..store import Payload

URL = "https://www.fixmystreet.com/open311/v2/requests.json"
# FixMyStreet body ids: only Newcastle (2529) is verified; the other Tyne and Wear councils are added in config once
# their ids are confirmed (the Open311 endpoint gives no bodies listing).
DEFAULT_BODIES = {"newcastle": 2529}


class FixMyStreetSource(CaptureSource):
    name = "fixmystreet"
    cadence_s = 6 * 3600

    def fetch(self, ctx: Context) -> list[Payload]:
        bodies = self.cfg.get("bodies") or DEFAULT_BODIES
        lookback = int(self.cfg.get("lookback_days", 7))
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback)
        out = []
        for name, body_id in bodies.items():
            params = {"jurisdiction_id": "fixmystreet.com", "agency_responsible": str(body_id),
                      "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "end_date": end.strftime("%Y-%m-%dT%H:%M:%SZ")}
            r = self.get(ctx, URL, params=params, timeout=60)
            n = None
            if r.ok:
                try:
                    n = len(r.json().get("service_requests") or [])
                except ValueError:
                    n = None
            out.append(self.payload_from_response(f"requests_{name}", r, ext="json", extra_meta={"body": name, "body_id": body_id}, n_items=n))
        return out

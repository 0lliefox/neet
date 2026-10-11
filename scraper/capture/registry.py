from __future__ import annotations

from typing import Any

from .base import CaptureSource


def all_sources(cfg: dict[str, Any]) -> list[CaptureSource]:
    """Instantiate every known capture source with its `capture.sources.<name>` config."""
    from .sources.rss import RSSSource
    from .sources.nswws import NSWWSSource
    from .sources.metoffice_hourly import MetOfficeHourlySource
    from .sources.bods import BODSSource
    from .sources.ea import EASource
    from .sources.ea_archive import EAArchiveSource
    from .sources.uo import UOSource
    from .sources.police_monthly import PoliceMonthlySource
    from .sources.streetmanager_archive import StreetManagerArchiveSource
    from .sources.onenetwork import OneNetworkSource
    from .sources.bluesky import BlueskySource
    from .sources.travel_updates import TravelUpdatesSource
    from .sources.fixmystreet import FixMyStreetSource

    classes = [RSSSource, NSWWSSource, MetOfficeHourlySource, BODSSource, EASource, EAArchiveSource, UOSource,
               PoliceMonthlySource, StreetManagerArchiveSource, OneNetworkSource, BlueskySource, TravelUpdatesSource,
               FixMyStreetSource]
    scfg = (cfg.get("sources") or {})
    out = []
    for cls in classes:
        c = scfg.get(cls.name, {}) or {}
        if c.get("enabled", True) is False:
            continue
        out.append(cls(c))
    return out

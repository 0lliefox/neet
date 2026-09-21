"""Importing this package registers every adapter (one module per capture source)."""
from . import (  # noqa: F401
    bluesky, bods, ea, metoffice_hourly, nswws, onenetwork, police, rss, streetmanager_archive, travel_updates, uo,
)

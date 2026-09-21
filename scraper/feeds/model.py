"""Evidence records (WS1): the normalised, provenance-bearing form of one thing the city said or observed.

Nothing here reasons. A record carries the human-authored text exactly as published (or none for telemetry),
the structured fields verbatim, where it applies, when it is valid, who produced it and how much to trust
that producer *class*. civic-verifier turns records into eFOL atoms; NEET only normalises provenance.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

KINDS = ("notice", "warning", "observation", "forecast", "crime_record", "post", "headline")
DOMAINS = ("roadworks", "transport", "weather", "flood", "crime", "other")
RELIABILITY_HINTS = ("official_signed", "official_operator", "media", "social")


@dataclass
class Place:
    """Where a record applies. `geometry` is WGS84 GeoJSON (Point / LineString / Polygon / MultiPolygon).
    `method` records how the place was obtained so civic-verifier can weight it: source_geometry |
    usrn | lexicon | none."""
    name: Optional[str] = None
    usrn: Optional[str] = None
    geometry: Optional[dict] = None
    admin_area: Optional[str] = None
    confidence: float = 0.0
    method: str = "none"


@dataclass
class EvidenceRecord:
    source: str
    kind: str
    domain: str
    text: Optional[str]
    structured: dict[str, Any]
    place: Place
    observed_at: Optional[str]
    fetched_at: Optional[str]
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    rendered_text: Optional[str] = None
    native_id: Optional[str] = None
    reliability_hint: str = "official_operator"
    provenance: dict[str, Any] = field(default_factory=dict)
    record_id: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown kind {self.kind!r}")
        if self.domain not in DOMAINS:
            raise ValueError(f"unknown domain {self.domain!r}")
        if self.reliability_hint not in RELIABILITY_HINTS:
            raise ValueError(f"unknown reliability hint {self.reliability_hint!r}")
        if not self.record_id:
            self.record_id = self.make_id(self.source, self.native_id, self.text, self.structured, self.valid_from)

    @staticmethod
    def make_id(source: str, native_id: Optional[str], text: Optional[str], structured: dict, valid_from: Optional[str]) -> str:
        """Deterministic: the same published item yields the same id across captures."""
        key = native_id if native_id else hashlib.sha1(
            (text or "").encode("utf-8") + json.dumps(structured, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        return hashlib.sha1(f"{source}|{key}|{valid_from or ''}".encode("utf-8")).hexdigest()[:20]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EvidenceRecord":
        d = dict(d)
        d["place"] = Place(**d.get("place", {}))
        return cls(**d)

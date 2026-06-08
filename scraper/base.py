from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class EvidenceItem:
    item_id: str          # e.g. "transport_001"
    domain: str           # "roadworks" | "weather" | "transport"
    evidence_text: str
    source_name: str      # e.g. "Nexus Metro Updates"
    source_page: str
    source_url: str
    date_time: str
    location: str
    claim_text: str = ""
    label: str = ""       # Supported | Refuted | Not Enough Evidence
    claim_type: str = ""  # paraphrase | negation | time change | location change |
                          # cause change | unsupported extra detail

    CSV_FIELDS: list[str] = field(default_factory=lambda: [
        "item_id", "domain", "evidence_text", "source_name", "source_page",
        "source_url", "date_time", "location", "claim_text", "label", "claim_type",
    ], repr=False, compare=False)

class BaseScraper(ABC):
    domain: str
    source_name: str
    source_url: str

    def __init__(self, max_items: int = 10) -> None:
        self.max_items = max_items
        self._counter = 0

    @abstractmethod
    def scrape(self) -> list[EvidenceItem]:
        """Fetch and return up to self.max_items EvidenceItem instances."""
        ...

    def _next_id(self) -> str:
        self._counter += 1
        return f"{self.domain}_{self._counter:03d}"

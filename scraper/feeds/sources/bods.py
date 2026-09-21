"""BODS SIRI-SX situation exchange (bus disruptions): kind=notice, domain=transport, the best native validity
of any source (`ValidityPeriod`), stop/line references but no coordinates (NaPTAN join deferred, WS1 A5)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Optional

from ..base import Adapter, iso_utc, register
from ..geo import place_hits
from ..model import EvidenceRecord, Place

NS = "{http://www.siri.org.uk/siri}"


def _text(el: Optional[ET.Element], path: str) -> Optional[str]:
    if el is None:
        return None
    found = el.find(path)
    return found.text.strip() if found is not None and found.text else None


class BodsSiriSxAdapter(Adapter):
    source = "bods_sirisx"
    reliability_hint = "official_operator"

    def records(self, body: bytes, meta: dict[str, Any]) -> list[EvidenceRecord]:
        root = ET.fromstring(body)
        out = []
        for sit in root.iter(f"{NS}PtSituationElement"):
            summary = _text(sit, f"{NS}Summary")
            desc = _text(sit, f"{NS}Description")
            advice = _text(sit, f"{NS}Advice")
            text = ". ".join(x.rstrip(".") for x in (summary, desc) if x) or None
            if advice:
                text = f"{text}. {advice}" if text else advice
            vp = sit.find(f"{NS}ValidityPeriod")
            vf, vt = iso_utc(_text(vp, f"{NS}StartTime")), iso_utc(_text(vp, f"{NS}EndTime"))
            stops = [s.text for s in sit.iter(f"{NS}StopPointRef") if s.text]
            lines = [l.text for l in sit.iter(f"{NS}LineRef") if l.text]
            names = [n.text for n in sit.iter(f"{NS}StopPointName") if n.text]
            ops = [o.text for o in sit.iter(f"{NS}OperatorRef") if o.text]
            hits = place_hits(" ".join(x for x in (text or "", *names) if x))
            out.append(EvidenceRecord(
                source=self.source, kind="notice", domain="transport", text=text,
                structured={"SituationNumber": _text(sit, f"{NS}SituationNumber"), "CreationTime": _text(sit, f"{NS}CreationTime"),
                            "Progress": _text(sit, f"{NS}Progress"), "Severity": _text(sit, f"{NS}Severity"),
                            "ReasonType": _text(sit, f"{NS}ReasonType") or (sit.find(f"{NS}MiscellaneousReason").text if sit.find(f"{NS}MiscellaneousReason") is not None else None),
                            "Planned": _text(sit, f"{NS}Planned"), "Summary": summary, "Description": desc, "Advice": advice,
                            "StopPointRefs": sorted(set(stops)), "StopPointNames": sorted(set(names)), "LineRefs": sorted(set(lines)), "OperatorRefs": sorted(set(ops))},
                place=Place(name=hits[0] if hits else (names[0] if names else None), confidence=0.6 if hits else (0.3 if names else 0.0),
                            method="lexicon" if hits or names else "none"),
                observed_at=iso_utc(_text(sit, f"{NS}CreationTime")), fetched_at=self.fetched_at(meta), valid_from=vf, valid_to=vt,
                native_id=_text(sit, f"{NS}SituationNumber"), reliability_hint=self.reliability_hint, provenance=self.provenance(meta)))
        return out


register(BodsSiriSxAdapter())

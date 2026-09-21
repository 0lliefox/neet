"""Met Office DataHub site-specific hourly forecast for the Newcastle point: one kind=forecast record per
time step. `rendered_text` reproduces the v1 sentence ("Met Office forecast for …: Cloudy, 13.35°C, …") for the
ablation; the structured block keeps every parameter of the step. Validity = the forecast hour."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..base import Adapter, iso_utc, load_json, register
from ..geo import point
from ..model import EvidenceRecord, Place

# Met Office significant weather codes (DataHub documentation)
WEATHER_CODES = {
    0: "Clear night", 1: "Sunny day", 2: "Partly cloudy (night)", 3: "Partly cloudy (day)", 5: "Mist", 6: "Fog", 7: "Cloudy",
    8: "Overcast", 9: "Light rain shower (night)", 10: "Light rain shower (day)", 11: "Drizzle", 12: "Light rain",
    13: "Heavy rain shower (night)", 14: "Heavy rain shower (day)", 15: "Heavy rain", 16: "Sleet shower (night)",
    17: "Sleet shower (day)", 18: "Sleet", 19: "Hail shower (night)", 20: "Hail shower (day)", 21: "Hail",
    22: "Light snow shower (night)", 23: "Light snow shower (day)", 24: "Light snow", 25: "Heavy snow shower (night)",
    26: "Heavy snow shower (day)", 27: "Heavy snow", 28: "Thunder shower (night)", 29: "Thunder shower (day)", 30: "Thunder",
}


def render(step: dict[str, Any], place_name: str) -> str:
    code = step.get("significantWeatherCode")
    cond = WEATHER_CODES.get(code, "Unknown conditions") if code is not None else "Unknown conditions"
    temp = step.get("screenTemperature")
    wind = step.get("windSpeed10m")
    pop = step.get("probOfPrecipitation")
    t = step.get("time", "")
    parts = [f"Met Office forecast for {place_name}: {cond}"]
    if temp is not None:
        parts.append(f"{temp}°C")
    if wind is not None:
        parts.append(f"wind {round(float(wind) * 2.23694, 2)} mph")   # m/s -> mph, as the v1 scraper rendered it
    if pop is not None:
        parts.append(f"{pop}% chance of precipitation at {t}")
    return ", ".join(parts) + "."


class MetOfficeHourlyAdapter(Adapter):
    source = "metoffice_hourly"
    reliability_hint = "official_signed"

    def records(self, body: bytes, meta: dict[str, Any]) -> list[EvidenceRecord]:
        d = load_json(body)
        feats = d.get("features") or []
        if not feats:
            return []
        feat = feats[0]
        props = feat.get("properties") or {}
        loc = (props.get("location") or {})
        name = loc.get("name") or "Newcastle upon Tyne"
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        geom = point(coords[0], coords[1]) if len(coords) >= 2 else None
        run = iso_utc(props.get("modelRunDate"))
        out = []
        for step in props.get("timeSeries") or []:
            t = iso_utc(step.get("time"))
            if not t:
                continue
            t_end = (datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) + timedelta(hours=1) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            out.append(EvidenceRecord(
                source=self.source, kind="forecast", domain="weather", text=None, rendered_text=render(step, name),
                structured={**step, "modelRunDate": props.get("modelRunDate"), "location_name": name,
                            "significantWeather": WEATHER_CODES.get(step.get("significantWeatherCode"))},
                place=Place(name=name, geometry=geom, confidence=1.0 if geom else 0.5, method="source_geometry" if geom else "lexicon"),
                observed_at=run, fetched_at=self.fetched_at(meta), valid_from=t, valid_to=t_end,
                native_id=f"mo_{name}_{props.get('modelRunDate')}_{step.get('time')}",
                reliability_hint=self.reliability_hint, provenance=self.provenance(meta)))
        return out


register(MetOfficeHourlyAdapter())

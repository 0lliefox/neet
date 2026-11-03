"""Evidence rows in CURB's columns, one function per domain. Wording comes from the v1 (CURB) formatters with the
city as a parameter; transport follows the page instead (Oliver, 22 Oct). Each function returns the CURB columns
`evidence_text, source_name, source_page, source_url, date_time, location`, or None when the record cannot be
rendered the way CURB's items were (the v1 scrapers skipped the same records)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from scraper.feeds.sources.travel_updates import parse_human_datetime
from scraper.scrapers.metoffice import SOURCE_URL as METOFFICE_NEWCASTLE_URL, format_forecast_evidence
from scraper.scrapers.nexus import SOURCE_URL as NEXUS_URL, _extract_location
from scraper.scrapers.police import API_BASE, PoliceScraper, _CATEGORY_LABELS
from scraper.scrapers.roadworks import MAP_URL as NEWCASTLE_ROADWORKS_URL, RoadworksScraper

UK = ZoneInfo("Europe/London")
CITIES = ("newcastle", "durham")

# CURB's values for Newcastle; Durham's per Oliver (3 Nov) where decided, else a setting (see `Settings`)
SOURCE_NAMES = {
    ("roadworks", "newcastle"): "Newcastle City Council Roadworks", ("roadworks", "durham"): "Durham County Council Roadworks",
    ("crime", "newcastle"): "Police.uk", ("crime", "durham"): "Police.uk",
    ("weather", "newcastle"): "Met Office", ("weather", "durham"): "Met Office",
    ("transport", "newcastle"): "Nexus Metro Updates",
}
CITY_LABEL = {"newcastle": "Newcastle", "durham": "Durham"}
WEATHER_PLACE = {"newcastle": "Newcastle upon Tyne", "durham": "Durham"}    # canonical; the DataHub says "Newcastle Upon Tyne"
MET_OFFICE_DURHAM_URL = "https://weather.metoffice.gov.uk/forecast/gcwzefp2c"   # "Durham (Durham) weather", loads (3 Nov)


def as_local_iso(utc_or_local: Optional[str], local_clock: bool) -> Optional[str]:
    """ISO 8601 with the UK offset, as CURB writes times. `local_clock`: the input's clock time is already UK local
    (travel_updates parses the page's local times into a `Z` string unchanged), so keep it and add the offset."""
    if not utc_or_local:
        return None
    dt = datetime.strptime(utc_or_local, "%Y-%m-%dT%H:%M:%SZ")
    if local_clock:
        dt = dt.replace(tzinfo=UK)
    else:
        dt = dt.replace(tzinfo=timezone.utc).astimezone(UK)
    return dt.isoformat()


def roadworks(rec: dict, city: str, durham_url: str) -> Optional[dict[str, str]]:
    s = rec["structured"]
    text = RoadworksScraper.format_tile_evidence({**s, "works_desc": rec.get("text") or s.get("works_desc")})   # the adapter keeps works_desc as `text`
    if text is None:
        return None
    return {"evidence_text": text, "source_name": SOURCE_NAMES[("roadworks", city)],
            "source_page": f"Roadworks in {CITY_LABEL[city]} Map (One.Network)",
            "source_url": NEWCASTLE_ROADWORKS_URL if city == "newcastle" else durham_url,
            "date_time": str(s.get("start_date") or ""), "location": str(s.get("road_name") or "").strip()}


def crime(rec: dict, city: str) -> Optional[dict[str, str]]:
    s = rec["structured"]
    street = str(s.get("street_name") or "").strip()
    month = str(s.get("month") or "")
    if not street or not month:
        return None
    slug = s.get("category") or "other-crime"
    category = _CATEGORY_LABELS.get(slug, slug.replace("-", " ").title())
    outcome = ((s.get("outcome_status") or {}) or {}).get("category", "") or ""
    # v1: the street is title-cased in the sentence ("On Or Near Haymarket"); `location` keeps Police.uk's map-point
    # label verbatim ("On or near Haymarket"), which LaSSI v4.4's curated arm reads (20 Oct brief, section 4)
    text = PoliceScraper._format_api_evidence(category, street.title(), month, outcome, city=CITY_LABEL[city])
    return {"evidence_text": text, "source_name": SOURCE_NAMES[("crime", city)], "source_page": "Street-level crime data",
            "source_url": f"{API_BASE}/crimes-street/all-crime", "date_time": f"{month}-01T00:00:00+00:00", "location": street}


def weather(rec: dict, city: str) -> Optional[dict[str, str]]:
    s = rec["structured"]
    if not s.get("time"):
        return None
    place = WEATHER_PLACE[city]
    url = METOFFICE_NEWCASTLE_URL if city == "newcastle" else MET_OFFICE_DURHAM_URL
    # the value the Met Office gives, with its true unit (Oliver, 3 Nov); CURB's v1 text said "mph" for m/s
    return {"evidence_text": format_forecast_evidence(s, place, wind_unit="m/s"), "source_name": SOURCE_NAMES[("weather", city)],
            "source_page": f"{place} Hourly Forecast", "source_url": url, "date_time": str(s["time"]), "location": place}


# -- transport ---------------------------------------------------------------------------------------------------
_END = re.compile(r"[.!?:;]$")
_NAV = ("for more details",)


def notice_lines(div_html: str) -> dict[str, Any]:
    """Heading, effective-from/until text and body lines of one `div.update-post`, keeping the page's line breaks
    (paragraphs and <br>). Lines that are only a link (timetable PDFs, "For more details") are navigation, not text."""
    from bs4 import BeautifulSoup
    div = BeautifulSoup(div_html, "lxml").select_one("div.update-post") or BeautifulSoup(div_html, "lxml")
    h2 = div.find("h2")
    title = re.sub(r"\s+", " ", h2.get_text(" ", strip=True)) if h2 else ""
    eff = {}
    for span in div.find_all("span"):
        t = re.sub(r"\s+", " ", span.get_text(" ", strip=True))
        m = re.match(r"(Effective from|Effective until):\s*(.+)", t)
        if m:
            eff[m.group(1)] = m.group(2)
    lines: list[str] = []
    for p in div.find_all("p"):
        chunks = re.split(r"<br\s*/?>", p.decode_contents(), flags=re.I)
        for chunk in chunks:
            frag = BeautifulSoup(chunk, "lxml")
            text = re.sub(r"\s+", " ", frag.get_text(" ", strip=True)).strip()
            if not text:
                continue
            links = " ".join(a.get_text(" ", strip=True) for a in frag.find_all("a"))
            if text.lower() in _NAV or (links and re.sub(r"\s+", " ", links).strip() == text):
                continue
            lines.append(text)
    return {"title": title, "from": eff.get("Effective from"), "until": eff.get("Effective until"), "lines": lines}


def sentence(line: str) -> str:
    line = line.strip()
    return line if _END.search(line) else line + "."


def transport_text(parts: dict[str, Any]) -> str:
    """Heading, then the date span as the page shows it, then the body, each line break ending a sentence. Never cut
    (Oliver, 3 Nov): a notice too long to use is dropped by the pool's `max_evidence_chars`, not shortened."""
    out = [sentence(parts["title"])] if parts["title"] else []
    if parts.get("from"):
        out.append(sentence(f"Effective from: {parts['from']}"))
    if parts.get("until"):
        out.append(sentence(f"Effective until: {parts['until']}"))
    out += [sentence(l) for l in parts["lines"]]
    return " ".join(out)


def page_time(text: Optional[str], year_hint: int) -> Optional[str]:
    """The page's "7 Sep 2026, 21:00" (UK local) as ISO with its offset. `parse_human_datetime` drops 24-hour times,
    and changing it would change travel_updates record_ids, so the page format is parsed here first."""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), "%d %b %Y, %H:%M").replace(tzinfo=UK).isoformat()
    except ValueError:
        local = parse_human_datetime(text, year_hint)
        return as_local_iso(local, local_clock=True) if local else None


def transport(rec: dict, parts: Optional[dict]) -> Optional[dict[str, str]]:
    """`date_time` is the notice's "Last updated" as written (UK local, with its offset; Oliver 3 Nov); without one,
    the item's first_seen. `date_time_fallback` says which was used."""
    if not parts or not (parts["title"] or parts["lines"]):
        return None
    text = transport_text(parts)
    s = rec["structured"]
    when = page_time(s.get("last_updated"), int((rec.get("fetched_at") or "2026")[:4]))
    fallback = when is None
    if fallback:
        when = as_local_iso(rec.get("first_seen") or rec.get("fetched_at"), local_clock=False)
    return {"evidence_text": text, "source_name": SOURCE_NAMES[("transport", "newcastle")], "source_page": "Metro Live Travel News",
            "source_url": NEXUS_URL, "date_time": when or "", "location": _extract_location(" ".join([parts["title"], *parts["lines"]])),
            "date_time_fallback": fallback}

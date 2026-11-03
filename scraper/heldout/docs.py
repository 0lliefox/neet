"""README.md and DATA_DICTIONARY.md for the export, and the template check against CURB.

The template check takes the first 5 CURB items of each domain (neet_v1.csv, S rows), recovers the source fields
from CURB's evidence text, renders them with NEET's export renderer and compares every CURB column. Transport is
rendered differently from CURB on purpose (Oliver, 22 Oct), so its check records the difference instead."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Optional

from scraper.scrapers.metoffice import WX_CODES
from scraper.scrapers.police import _CATEGORY_LABELS

from . import render

FIELDS = ("evidence_text", "source_name", "source_page", "source_url", "date_time", "location")
_CRIME = re.compile(r"^(?P<cat>.+?) recorded on (?P<street>.+), (?P<city>Newcastle|Durham), in (?P<month>\w+ \d{4})\.(?: Outcome: (?P<out>.+)\.)?$")
_WEATHER = re.compile(r"^Met Office forecast for (?P<place>[^:]+): (?P<cond>[^,]+), (?P<temp>-?[\d.]+)°C, wind (?P<wind>[\d.]+) mph, "
                      r"(?P<pop>\d+)% chance of precipitation at (?P<time>\S+)\.$")
_ROAD_TAIL = re.compile(r"^(?P<body>.*?)(?: Traffic management: (?P<tm>[^.]+)\.)?(?: (?P<delay>Delays \w+)\.)? Work expected to end (?P<end>\d{4}-\d{2}-\d{2})\.$", re.S)


def _curb_rows(curb: Path) -> list[dict]:
    with curb.open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r["item_id"].endswith("_S")]


def _from_curb(row: dict) -> Optional[dict]:
    """NEET's rendering of the fields recovered from one CURB item, or None if CURB's text does not parse."""
    d, t = row["domain"], row["evidence_text"]
    if d == "crime":
        m = _CRIME.match(t)
        if not m:
            return None
        slug = {v: k for k, v in _CATEGORY_LABELS.items()}.get(m["cat"], m["cat"].lower().replace(" ", "-"))
        from datetime import datetime
        month = datetime.strptime(m["month"], "%B %Y").strftime("%Y-%m")
        rec = {"structured": {"category": slug, "street_name": row["location"], "month": month,
                              "outcome_status": {"category": m["out"]} if m["out"] else None}}
        return render.crime(rec, m["city"].lower())
    if d == "weather":
        m = _WEATHER.match(t)
        if not m:
            return None
        code = {v: k for k, v in WX_CODES.items()}.get(m["cond"])
        step = {"time": m["time"], "screenTemperature": float(m["temp"]), "windSpeed10m": float(m["wind"]),
                "probOfPrecipitation": int(m["pop"]), "significantWeatherCode": code}
        return render.weather({"structured": step}, "newcastle" if "Newcastle" in m["place"] else "durham")
    if d == "roadworks":
        head = re.match(r"^Roadworks on (?P<road>[^:]+): (?P<rest>.*)$", t, re.S)
        tail = _ROAD_TAIL.match(head["rest"]) if head else None
        if not tail:
            return None
        body = tail["body"]
        desc = body[:-1] if body.endswith(".") else body
        props = {"road_name": head["road"], "works_desc": desc, "traffman": tail["tm"] or "No carriageway incursion",
                 "delay": (tail["delay"] or "").lower(), "end_date": tail["end"], "start_date": row["date_time"]}
        return render.roadworks({"structured": props}, "newcastle", "")
    return None


def template_checks(curb: Path, n: int = 5) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    rows = _curb_rows(curb)
    for domain in ("crime", "roadworks", "weather", "transport"):
        res = []
        for row in [r for r in rows if r["domain"] == domain][:n]:
            if domain == "transport":
                res.append({"item": row["item_id"][:-2], "result": "differs by design",
                            "detail": "CURB glued heading and body and cut at 250 characters; NEET renders heading, date span "
                                      "and body lines as sentences (see Transport)"})
                continue
            mine = _from_curb(row)
            if mine is None:
                res.append({"item": row["item_id"][:-2], "result": "CURB text did not parse", "detail": ""}); continue
            diffs = [f for f in FIELDS if mine[f] != row[f]]
            res.append({"item": row["item_id"][:-2], "result": "identical" if not diffs else "differs: " + ", ".join(diffs),
                        "detail": "; ".join(f"{f}: CURB {row[f]!r} vs NEET {mine[f]!r}" for f in diffs)})
        out[domain] = res
    return out


def _meta(out: Path, rel: str) -> Optional[dict]:
    p = out / (rel + ".meta.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def write_docs(out: Path, curb: Path) -> None:
    checks = template_checks(curb)
    lines = []
    for domain, res in checks.items():
        for r in res:
            lines.append(f"| {domain} | {r['item']} | {r['result']} | {r['detail'].replace('|', '/')} |")
    (out / "DATA_DICTIONARY.md").write_text(DATA_DICTIONARY.replace("{{TEMPLATE_CHECKS}}", "\n".join(lines)), encoding="utf-8")

    from .cli import STAGE_FILES
    stages = []
    for stage, rel in STAGE_FILES.items():
        m = _meta(out, rel)
        if m is None:
            stages.append(f"- **{stage}** (`{rel}`): not built")
            continue
        stages.append(f"- **{stage}** (`{rel}`), built {m['created_at']} at commit `{m['code_commit'][:12]}`; "
                      f"settings `{json.dumps(m['settings'], sort_keys=True)}`; output sha256 `{(m.get('output_sha256') or '')[:16]}`")
        for k, v in (m.get("inputs") or {}).items():
            stages.append(f"  - input {k}: `{v['path']}` sha256 `{v['sha256'][:16]}`")
        stages.append(f"  - counts: `{json.dumps(m['counts'], sort_keys=True)}`")
    (out / "README.md").write_text(README.replace("{{STAGES}}", "\n".join(stages)), encoding="utf-8")


README = """# Held-out evidence set (LaSSI v4.4, thesis chapter 6)

Civic evidence that NEET first captured after the LaSSI v4.4 freeze (Newcastle, temporal hold-out) or from Durham,
which CURB never covers (geographic hold-out), in CURB's format. NEET builds the evidence pool, the seeded sample
and the claim sheet; Oliver writes every claim. DATA_DICTIONARY.md documents every column and rule.

## Rebuild

Each stage is one command, reads the previous stage's file under the same `--out-dir`, and writes a `.meta.json`
sidecar with its settings, seed, input hashes and code commit.

```
neet heldout records --root "<Drive>/CivicVerifierData" --out-dir ~/heldout_build [--before <ISO UTC>]
neet heldout pool    --root "<Drive>/CivicVerifierData" --out-dir ~/heldout_build --window-start <ISO UTC> \\
                     --curb ~/neet_v1.csv --dev ~/official_sample.json
neet heldout sample  --out-dir ~/heldout_build --seed <N>
neet heldout claims  --out-dir ~/heldout_build --seed <N>
neet heldout heldout --out-dir ~/heldout_build --sheet <filled claim sheet> --check ~/heldout_check.py
neet heldout docs    --out-dir ~/heldout_build --curb ~/neet_v1.csv
```

- Window start: **OPEN** (Oliver): the v4.4 freeze commit (2 Nov ~16:00 UTC) or the v4.4 tag.
- Exclusions: CURB's 30 items; the 40 development items in official_sample.json and every item whose lineage links
  to one; works first seen in a new onenetwork tile's first capture; onenetwork works with a Street Manager permit
  event before the window start.
- The raw capture store is never modified; everything here is rebuilt from it.

## This build

{{STAGES}}
"""

DATA_DICTIONARY = """# Data dictionary: held-out evidence set

## Files

| File | One row per | Columns |
|---|---|---|
| `00_records/records.jsonl` | captured version of a record | NEET EvidenceRecord, with `item_key`, `first_seen`, `baseline` |
| `01_pool/pool.csv` | eligible item (earliest captured version) | CURB columns (claim cells empty) + provenance + stratum |
| `02_sample/sample.csv` | sampled item | as pool, with `item_id` = `test_<city>_<domain>_<nnn>` |
| `03_claims/claim_sheet.csv` | sampled item | `item_id, domain, city, evidence_text, date_time, location`, then for S, R and N the pre-drawn strategy, its label and an empty claim cell |
| `03_claims/heldout.csv` | claim (3 per item) | CURB's 11 columns in CURB's order, then `city, record_id, item_key, capture_file, first_seen, valid_from, valid_to`; `item_id` = `test_<city>_<domain>_<nnn>_{S,R,N}` |

## Columns

| Column | Content |
|---|---|
| `item_id` | `test_<city>_<domain>_<nnn>` (sample), plus `_S`, `_R`, `_N` in heldout.csv. Never `ho_` or `dev_`. |
| `domain` | `roadworks`, `transport`, `crime`, `weather` |
| `evidence_text` | rendered evidence (templates below) |
| `source_name` | `Newcastle City Council Roadworks`, `Durham County Council Roadworks`, `Police.uk`, `Met Office`, `Nexus Metro Updates` |
| `source_page` | `Roadworks in <City> Map (One.Network)`, `Street-level crime data`, `<Place> Hourly Forecast`, `Metro Live Travel News` |
| `source_url` | CURB's URLs for Newcastle. Durham: the public pages, checked to load on 3 Nov 2026: `https://weather.metoffice.gov.uk/forecast/gcwzefp2c` ("Durham (Durham) weather") and `https://www.durham.gov.uk/roadworks` (Durham County Council, "Find out about roadworks in your area"). LaSSI does not read this column. |
| `date_time` | the record's own time as written, UK local time with its offset (in the window UK time is UTC): roadworks `start_date`; crime `<month>-01T00:00:00+00:00`; weather the forecast step's `time` (`YYYY-MM-DDTHH:MMZ`, as CURB); transport the notice's "Last updated" (Oliver, 3 Nov; without one, the item's `first_seen`, counted as `date_time_fallback` in the pool sidecar) |
| `location` | roadworks `road_name`; crime Police.uk's map-point label verbatim (`On or near ...`); weather the canonical place; transport the first Metro station named (v1 `_extract_location`, else `Tyne and Wear Metro`) |
| `claim_text`, `label`, `claim_type` | empty until Oliver writes the claims; label fixed by slot (S Supported, R Refuted, N Not Enough Evidence), claim_type = the drawn strategy |
| `city` | `newcastle` or `durham` |
| `record_id` | NEET record id of the rendered version (unchanged by the lineage fixes, so official_sample.json still matches) |
| `item_key` | the item's lineage key (`neet feeds build`); eligibility and exclusions are per item_key |
| `capture_file` | the raw capture the rendered version came from, relative to the data root |
| `first_seen` | earliest capture of any version of the item (UTC) |
| `valid_from`, `valid_to` | NEET validity interval (UTC) |
| `model_run`, `month`, `works_ref`, `notice_type` | stratum helpers: Met Office model run, Police.uk month, one.network works reference(s), page notice type |

## Slices and eligibility

| Slice | Source (capture) | Filter |
|---|---|---|
| Roadworks, Newcastle | `onenetwork` | `pub_name == "Newcastle City Council"` |
| Roadworks, Durham | `onenetwork` | `pub_name == "Durham"` |
| Transport, Metro | `travel_updates` (`updates` payload; text from the stored page HTML) | none |
| Crime, Newcastle | `police`, tag `newcastle_centre_<month>` | month first captured inside the window |
| Crime, Durham | `police`, tag `durham_<month>` | any month captured (setting `durham_crime_all_captured`, Oliver 3 Nov; needs Ellis's agreement) |
| Weather, Newcastle / Durham | `metoffice_hourly`, tags `hourly_newcastle` / `hourly_durham` | one forecast step per model run (drawn at sample time) |

An item is eligible when its `first_seen` is at or after the window start (and before `--window-end`, for mock
windows). The version rendered is the earliest one captured.

Exclusions, applied in this order and counted in `pool.csv.meta.json`:
`curb` (CURB's 30 items: same evidence text, or same domain, date_time and location); `dev` (lineage contains one
of the 40 record_ids in official_sample.json); `baseline` (first seen in the first capture of a newly added
onenetwork tile, i.e. tile 10/507/325 added 3 Nov 2026 for Durham city centre); `street_manager` (onenetwork works
whose `works_ref` has a Street Manager permit event before the window start, read from the monthly permit
archives' member names, so a works first published while a tile was down is not new); `not_renderable`;
`too_long` (rendered evidence longer than `max_evidence_chars` characters: the item is dropped, never cut; Oliver,
3 Nov: only notices reasonable to parse as full sentences. Off by default; the value used is in the pool sidecar).

## Lineage: what counts as one item

`record_id` names one version of a record. `neet feeds build` groups versions into items (`item_key`) and sets
`first_seen` to the earliest capture of any version (scraper/feeds/lineage.py):

- onenetwork: the same `works_ref` or the same tile feature `id` (a rescheduled works keeps its id; one works can
  have several feature ids).
- police, metoffice_hourly: the record itself (a crime is a persistent id in a month; a forecast is a model run's
  step).
- travel_updates: rule **P5** (Oliver, 3 Nov 2026), below.

### P5 (Metro notices)

The page gives notices no id, and the site reuses generic titles ("Service suspension", "Service Update: Yellow
Line") for unrelated incidents days apart. A version is linked to an earlier one when

1. their bodies are identical (whitespace and case ignored), at any time: the same evidence text was already seen; or
2. the earlier version left the page in the capture just before this one appeared, or at most 8 hours before, and
   - they are similar: title similarity >= 0.75 or body similarity >= 0.5 (Python difflib ratio, normalised text), and
   - they keep the same title, or they share a place (a lexicon station or line, or neither names one), the page's
     notice type (live-update / planned-work / ...) and, unless both are live updates, the same "Effective from" text.

Status updates on one incident (suspended -> resumed, closed -> reopened) are the same notice when P5 links them
(Oliver, 3 Nov: an incident live before the window must not enter through a later update, and sampled items stay
independent); an update more than 8 hours after the earlier version left the page is new.

**Check (pre-window captures only, 20 Sep - 2 Nov 16:00 UTC, 368 captures).** 158 pairs of versions were labelled
by reading them: every consecutive pair inside the lineages of the earlier rule (title or body shared), and every
pair where a notice appeared in the capture where another disappeared. 80 rewrites of one notice (R), 13 status
updates on one incident (U), 65 genuinely new notices (N).

| Rule | Rewrites linked | Updates linked | New notices lost |
|---|---|---|---|
| title or body shared, any time (2 Nov rule) | 68/80 | 9/13 | 45/65 |
| P5 without the 8 h window (previous capture only) | 75/80 | 8/13 | 2/65 |
| **P5 (8 h window)** | **80/80** | **11/13** | **4/65** |

On the same captures, P5 gives 152 items (105 first seen 1 Oct - 2 Nov, 3.4 a day) against 124 (2.5 a day) for the
2 Nov rule. Caveats: one annotator (the NEET agent), labels made from the text; the thresholds were chosen on the
same 158 pairs, so the table is optimistic.

## Rendering templates

Wording reuses the v1 (CURB) formatters with the city as a parameter (`scraper/scrapers/*.py`).

- **Roadworks** (`RoadworksScraper.format_tile_evidence`): `Roadworks on {road_name}: {works_desc}. [Traffic
  management: {traffman}.] [{Delay}.] [Work expected to end {end_date[:10]}.]`; traffic management is omitted when
  it is "No carriageway incursion"; works without a description of 10+ characters or a road name are not rendered.
  A description ending in "." gives "..", as in CURB.
- **Crime** (`PoliceScraper._format_api_evidence`): `{Category} recorded on {Street Title-Cased}, {City}, in {Month
  YYYY}.[ Outcome: {outcome}.]` CURB's `location` is title-cased like its sentence ("On Or Near Haymarket"); the
  export keeps Police.uk's label verbatim ("On or near Haymarket") as the 20 Oct brief requires (confirmed 3 Nov:
  LaSSI v4.4's curated arm strips "on or near" ignoring case and compares names in lower case).
- **Weather** (`format_forecast_evidence`): `Met Office forecast for {Place}: {condition}, {temp}°C, wind {wind}
  mph, {pop}% chance of precipitation at {time}.` with canonical places "Newcastle upon Tyne" and "Durham". As in
  CURB, `wind` is the DataHub's `windSpeed10m`, which is in m/s, printed with the label "mph". OPEN: keep CURB's
  convention or convert (x 2.23694).
- **Transport** (differs from CURB by design, Oliver 22 Oct): the notice's heading, then its date span as the page
  shows it, then the body, each line break ending a sentence:
  `{heading}. Effective from: {from}. [Effective until: {until}.] {body line 1}. {body line 2}. ...`
  Lines that are only a link (timetable PDFs, "For more details") are left out. CURB's v1 text glued the heading
  onto the body, joined the body's lines with spaces and cut at 250 characters, sometimes mid-sentence. Evidence
  is never cut (Oliver, 3 Nov); `max_evidence_chars` drops long items instead. All notices on the page are in
  scope, bus and ferry notices included (Oliver, 3 Nov); the domain stays `transport`.

## Template check against CURB (5 items per domain, `neet heldout docs`)

CURB's fields are recovered from its evidence text and rendered again with the export's renderer; every CURB column
is compared.

| Domain | CURB item | Result | Detail |
|---|---|---|---|
{{TEMPLATE_CHECKS}}

## Draw

- `sample`: seeded (`--seed`), stratified by domain x city with the targets of the 20 Oct brief (Newcastle roadworks
  18, Metro 18, crime 17, weather 17; Durham crime 10, weather 10, roadworks 10). Weather draws model runs, then one
  step of each run. A stratum smaller than its target is taken whole (reported in the sidecar).
- `claims`: per domain (both cities together), each slot's strategies are allocated in CURB's proportions (largest
  remainder) and shuffled with the seed. CURB's mix (per 7 or 8 items): crime S approximate_paraphrase 7; R
  outcome_mismatch 3, location_mismatch 2, negation 2; N unsupported_extra_detail 7. Roadworks S direct_support 7;
  R location_mismatch 4, time_mismatch 2, outcome_mismatch 1; N unsupported_extra_detail 6, cause_mismatch 1.
  Transport S direct_support 5, approximate_paraphrase 3; R negation 5, location_mismatch 3; N cause_mismatch 5,
  unsupported_extra_detail 2, time_mismatch 1. Weather S approximate_paraphrase 7, direct_support 1; R negation 7,
  location_mismatch 1; N time_mismatch 7, unsupported_extra_detail 1.
- `heldout`: three rows per item from the filled claim sheet; it refuses to write while any claim is empty, a label
  was changed or the evidence differs from the sample, then runs LaSSI's `heldout_check.py` unchanged.
"""

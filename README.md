# NEET: North East Evidence Tracker

_"NEET" comes from the Geordie for "night"._

Scrapes live Newcastle / Tyne and Wear civic data and outputs a CSV for manual claim annotation.

## Setup

```bash
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env   # add your Met Office API key (optional)
```

## Run

```bash
# All sources → data/dataset.csv
python -m scraper.runner

# Specific sources
python -m scraper.runner --sources nexus flood metoffice air_quality bridges bus police tyne_tunnels

# Override output path or item limit
python -m scraper.runner --output data/test.csv --max-per-source 5
```

Available sources: `nexus`, `flood`, `metoffice`, `roadworks`, `traffic`, `news`, `air_quality`, `bridges`, `bus`, `police`, `tyne_tunnels`, `all`

## Output

Each evidence snippet produces **3 CSV rows** (Supported / Refuted / Not Enough Evidence) with `claim_text` and `claim_type` left blank for manual completion.

| Column | Description |
|---|---|
| `item_id` | e.g. `roadworks_001_S` |
| `domain` | `roadworks`, `weather`, or `transport` |
| `evidence_text` | Scraped fact snippet |
| `claim_text` | **Fill in manually** |
| `label` | Pre-populated: Supported / Refuted / Not Enough Evidence |
| `claim_type` | **Fill in manually** (paraphrase, negation, time change, …) |

## Configuration

All parameters are in `config.yaml` — source URLs, tile coordinates, item limits, domain targets, and the traffic alert place-name filter.

## Sources

| Source | Domain | Method |
|---|---|---|
| Nexus Metro Updates | transport | requests + BS4 |
| Traffic England | transport | Playwright → DATEX II XML |
| Newcastle Roadworks | roadworks | One.Network PBF tiles → Playwright → council news |
| Met Office | weather | DataPoint API → Playwright |
| Environment Agency Floods | weather | JSON REST API |
| Newcastle Council News | roadworks | requests + BS4 |
| Sensor.Community (Air Quality) | weather | JSON REST API |
| Newcastle / Gateshead Bridges | transport | requests + BS4 → DATEX II |
| Go North East / Arriva North East | transport | requests + BS4 → Playwright |
| Northumbria Police (Crime) | crime | JSON REST API |
| Tyne Tunnels (TT2) | transport | requests + BS4 → DATEX II |

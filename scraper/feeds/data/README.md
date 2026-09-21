# Derived data files

- `gazetteer.json.gz` — named places and roads of the Tyne and Wear study area (bbox in `scraper/feeds/gazetteer.py`),
  built by `python -m scraper.feeds.gazetteer build` from
  * GeoNames GB dump (https://download.geonames.org/export/dump/GB.zip), licence CC BY 4.0 (© GeoNames contributors), and
  * OS Open Names, 100 km tiles NZ (https://osdatahub.os.uk/downloads/open/OpenNames), Contains OS data © Crown copyright and database right 2026, Open Government Licence v3.0.
  Rebuild whenever the lexicon bbox changes; the build is deterministic given the two inputs (record their download dates in the commit message).

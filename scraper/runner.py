"""CLI runner for the NEET dataset pipeline.

Usage:
    python -m scraper.runner
    python -m scraper.runner --sources nexus flood --output data/test.csv
    python -m scraper.runner --sources nexus --max-per-source 5
    python -m scraper.runner --config path/to/config.yaml

Available sources:
    nexus, flood, metoffice, roadworks, traffic, news,
    tyne_tunnels, air_quality, bus, bridges, all

Configuration is loaded from config.yaml in the project root.
All values can be overridden with CLI flags.
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

ALL_POSSIBLE_SOURCES = [
    "nexus", "flood", "metoffice", "roadworks", "traffic", "news",
    "tyne_tunnels", "air_quality", "bus", "bridges", "police",
]

def _load_scrapers(sources: list[str], max_per_source: int, cfg: dict):
    scrapers = []

    if "nexus" in sources:
        from scraper.scrapers.nexus import NexusScraper
        n = cfg.get("sources", {}).get("nexus", {})
        scrapers.append(NexusScraper(max_items=n.get("max_items", max_per_source)))

    if "flood" in sources:
        from scraper.scrapers.flood import FloodScraper
        n = cfg.get("sources", {}).get("flood", {})
        scrapers.append(FloodScraper(
            max_items=n.get("max_items", max_per_source),
            areas=n.get("areas"),
        ))

    if "metoffice" in sources:
        from scraper.scrapers.metoffice import MetOfficeScraper
        n = cfg.get("sources", {}).get("metoffice", {})
        scrapers.append(MetOfficeScraper(
            max_items=n.get("max_items", max_per_source),
            location_id=n.get("datapoint_location_id"),
            lat=n.get("latitude"),
            lon=n.get("longitude"),
        ))

    if "roadworks" in sources:
        from scraper.scrapers.roadworks import RoadworksScraper
        n = cfg.get("sources", {}).get("roadworks", {})
        on = n.get("one_network", {})
        scrapers.append(RoadworksScraper(
            max_items=n.get("max_items", max_per_source),
            tile_zoom=on.get("tile_zoom"),
            tiles=on.get("tiles"),
            organisation_id=on.get("organisation_id"),
            date_range_days_past=on.get("date_range_days_past"),
            date_range_days_future=on.get("date_range_days_future"),
        ))

    if "traffic" in sources:
        from scraper.scrapers.traffic import TrafficScraper
        n = cfg.get("sources", {}).get("traffic", {})
        scrapers.append(TrafficScraper(
            max_items=n.get("max_items", max_per_source),
            ne_patterns=n.get("ne_filter_patterns"),
        ))

    if "news" in sources:
        from scraper.scrapers.council_news import CouncilNewsScraper
        n = cfg.get("sources", {}).get("council_news", {})
        scrapers.append(CouncilNewsScraper(
            max_items=n.get("max_items", max_per_source),
            keywords=n.get("roadworks_keywords"),
        ))

    if "tyne_tunnels" in sources:
        from scraper.scrapers.tyne_tunnels import TyneTunnelsScraper
        n = cfg.get("sources", {}).get("tyne_tunnels", {})
        scrapers.append(TyneTunnelsScraper(max_items=n.get("max_items", max_per_source)))

    if "air_quality" in sources:
        from scraper.scrapers.air_quality import AirQualityScraper
        n = cfg.get("sources", {}).get("air_quality", {})
        scrapers.append(AirQualityScraper(max_items=n.get("max_items", max_per_source)))

    if "bus" in sources:
        from scraper.scrapers.bus import BusScraper
        n = cfg.get("sources", {}).get("bus", {})
        scrapers.append(BusScraper(max_items=n.get("max_items", max_per_source)))

    if "bridges" in sources:
        from scraper.scrapers.bridges import BridgesScraper
        n = cfg.get("sources", {}).get("bridges", {})
        scrapers.append(BridgesScraper(max_items=n.get("max_items", max_per_source)))

    if "police" in sources:
        from scraper.scrapers.police import PoliceScraper
        n = cfg.get("sources", {}).get("police", {})
        scrapers.append(PoliceScraper(
            max_items=n.get("max_items", max_per_source),
            lat=n.get("latitude", 54.9781),
            lon=n.get("longitude", -1.6162),
            date=n.get("date"),
        ))

    return scrapers

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape North East civic evidence snippets for the NEET dataset."
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml (default: config.yaml in project root)",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        default=None,
        choices=ALL_POSSIBLE_SOURCES + ["all"],
        metavar="SOURCE",
        help=f"Sources to run. Choices: {', '.join(ALL_POSSIBLE_SOURCES + ['all'])}",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path",
    )
    parser.add_argument(
        "--max-per-source",
        type=int,
        default=None,
        dest="max_per_source",
        help="Maximum evidence items per source (overrides config)",
    )
    args = parser.parse_args(argv)

    from scraper.config import load as load_config
    cfg = load_config(args.config)
    pipeline_cfg = cfg.get("pipeline", {})

    default_sources = pipeline_cfg.get("default_sources", ["nexus", "flood", "metoffice", "roadworks", "traffic"])
    max_per_source = args.max_per_source or pipeline_cfg.get("max_items_per_source", 10)
    output = args.output or pipeline_cfg.get("output", "data/dataset.csv")
    claim_labels = cfg.get("claims", {}).get("labels", ["Supported", "Refuted", "Not Enough Evidence"])
    domain_targets = cfg.get("domains", {}).get("targets", {"roadworks": 10, "weather": 10, "transport": 10})

    if args.sources is None:
        sources = default_sources
    elif "all" in args.sources:
        sources = default_sources
    else:
        sources = args.sources

    print(f"Config:  {args.config or 'config.yaml (default)'}")
    print(f"Sources: {', '.join(sources)}")
    print(f"Output:  {output}")
    print(f"Max per source: {max_per_source}")
    print()

    scrapers = _load_scrapers(sources, max_per_source, cfg)
    all_items = []

    for scraper in scrapers:
        print(f"  Scraping {scraper.source_name} ...", end=" ", flush=True)
        try:
            items = scraper.scrape()
            all_items.extend(items)
            print(f"{len(items)} items")
        except Exception as exc:
            print(f"FAILED ({exc})")
            logger.exception("Scraper %s raised an exception", type(scraper).__name__)

    seen: set[str] = set()
    unique_items = []
    for item in all_items:
        key = item.evidence_text.strip().lower()
        if key not in seen:
            seen.add(key)
            unique_items.append(item)
    if len(unique_items) < len(all_items):
        print(f"  (deduplicated {len(all_items) - len(unique_items)} duplicate items)")
    all_items = unique_items

    domain_counters: dict[str, int] = {}
    for item in all_items:
        domain_counters[item.domain] = domain_counters.get(item.domain, 0) + 1
        item.item_id = f"{item.domain}_{domain_counters[item.domain]:03d}"

    print()
    _print_report(all_items, domain_targets, claim_labels)

    if not all_items:
        print("No items collected. Exiting without writing CSV.")
        return 1

    from scraper.writer import write_csv
    write_csv(all_items, output, claim_labels=claim_labels)
    return 0

def _print_report(items, domain_targets: dict, claim_labels: list[str]) -> None:
    from collections import Counter
    domain_counts = Counter(item.domain for item in items)
    total_evidence = len(items)
    total_rows = total_evidence * len(claim_labels)

    print("── Collection report ──────────────────────")
    for domain, target in domain_targets.items():
        count = domain_counts.get(domain, 0)
        status = "OK" if count >= target else f"WARN: only {count}/{target}"
        print(f"  {domain:<12} {count:>3} evidence items  {status}")
    print(f"  {'TOTAL':<12} {total_evidence:>3} evidence items")
    print(f"  {'CSV rows':<12} {total_rows:>3}  ({total_evidence} × {len(claim_labels)} claims)")

    if total_evidence < sum(domain_targets.values()):
        print()
        print("  Some domains are below target. Run with --sources <name> to debug,")
        print("  or add items manually to the CSV.")
    print("───────────────────────────────────────────")
    print()

if __name__ == "__main__":
    sys.exit(main())

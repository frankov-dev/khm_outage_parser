"""
CLI entry point for scraping hoe.com.ua outage schedules and exporting to JSON.
"""

import argparse
import logging
import sys
from pathlib import Path

from src.exporter import JsonExporter


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape Khmelnytskyi outage schedules and save as JSON."
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("docs"),
        help="Directory where outages.json and latest.json will be saved (default: docs)",
    )
    parser.add_argument(
        "--max-days",
        type=int,
        default=14,
        help="Maximum number of historical dates to keep in outages.json (default: 14)",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Exit with code 0 even if no active schedule was found on the website",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    exporter = JsonExporter(
        output_dir=args.output_dir,
        max_days_history=args.max_days,
    )
    result = exporter.run()

    if result is None:
        if args.allow_empty:
            logging.warning("No active schedules found; keeping existing JSON files.")
            return 0
        logging.error("Failed to fetch or parse any outage schedules.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

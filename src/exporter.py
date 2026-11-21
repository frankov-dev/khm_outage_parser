"""
JSON exporter for Khmelnytskyi power outage schedules.

Orchestrates scraping from hoe.com.ua, parsing schedule blocks, and saving
structured JSON files ready to be served via GitHub Pages.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.parser import Parser
from src.scraper import KYIV_TZ, Scraper

logger = logging.getLogger(__name__)


def calculate_total_hours(intervals: list[dict[str, str]]) -> float:
    """Calculate total outage duration in hours for a list of intervals."""
    total_minutes = 0
    for interval in intervals:
        try:
            sh, sm = map(int, interval["start"].split(":"))
            eh, em = map(int, interval["end"].split(":"))
            diff = (eh * 60 + em) - (sh * 60 + sm)
            if diff > 0:
                total_minutes += diff
        except (KeyError, ValueError, AttributeError):
            continue
    return round(total_minutes / 60, 2)


class JsonExporter:
    """
    Scrapes, parses, and exports outage schedules to JSON files.

    Generates:
        - outages.json: Full dataset containing all available dates and queues
        - latest.json: Convenience file with the most recent schedule date
    """

    def __init__(
        self,
        output_dir: Path | str = "docs",
        scraper: Optional[Scraper] = None,
        parser: Optional[Parser] = None,
        max_days_history: int = 14,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.scraper = scraper or Scraper()
        self.parser = parser or Parser()
        self.max_days_history = max_days_history

    def _load_existing_payload(self) -> Optional[dict[str, Any]]:
        """Load previously saved payload from outages.json if available."""
        outages_file = self.output_dir / "outages.json"
        if not outages_file.exists():
            return None
        try:
            with outages_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("schedules"), dict):
                return data
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Could not read existing {outages_file}: {e}")
        return None

    def _load_existing_schedules(self) -> dict[str, Any]:
        """Load previously saved schedules to preserve history across runs."""
        payload = self._load_existing_payload()
        if payload is None:
            return {}
        return payload["schedules"]

    def run(self) -> Optional[dict[str, Any]]:
        """
        Fetch latest schedules, parse them, merge with history, and save JSON files.

        Returns:
            The exported payload dict, or None if no schedules could be extracted.
        """
        html = self.scraper.fetch()
        blocks = self.scraper.extract_blocks(html) if html else []

        if not blocks:
            logger.info("No schedule blocks found on main page; trying news fallback")
            fallback_html = self.scraper.fetch_latest_from_news()
            if fallback_html:
                blocks = self.scraper.extract_blocks(fallback_html)

        if not blocks:
            logger.warning("No schedule blocks could be extracted from hoe.com.ua")
            return None

        existing_payload = self._load_existing_payload()
        existing_schedules: dict[str, Any] = (
            existing_payload["schedules"] if existing_payload is not None else {}
        )
        schedules = dict(existing_schedules)
        updated_dates: list[str] = []
        seen_in_run: set[str] = set()

        for block in blocks:
            parsed = self.parser.parse_block(block)
            date_str = parsed.get("date")
            raw_queues = parsed.get("queues")

            if not date_str or not raw_queues:
                continue

            # Newest update for a date appears first on hoe.com.ua; keep the first valid block
            if date_str in seen_in_run:
                continue

            formatted_queues: dict[str, Any] = {}
            for queue_id in sorted(raw_queues.keys()):
                intervals = raw_queues[queue_id]
                formatted_queues[queue_id] = {
                    "intervals": intervals,
                    "total_hours_off": calculate_total_hours(intervals),
                }

            schedules[date_str] = {
                "date": date_str,
                "operational_message": parsed.get("message"),
                "queues": formatted_queues,
            }
            seen_in_run.add(date_str)
            updated_dates.append(date_str)
            logger.info(
                f"Parsed schedule for {date_str} ({len(formatted_queues)} queues)"
            )

        if not updated_dates:
            logger.warning("Blocks were found, but no valid queues could be parsed")
            return None

        # Keep only the most recent `max_days_history` dates, sorted descending
        sorted_dates = sorted(schedules.keys(), reverse=True)[: self.max_days_history]
        trimmed_schedules = {d: schedules[d] for d in sorted_dates}

        latest_path = self.output_dir / "latest.json"
        if (
            existing_payload is not None
            and trimmed_schedules == existing_schedules
            and latest_path.exists()
        ):
            logger.info("Schedules unchanged; skipping JSON overwrite.")
            return existing_payload

        now_iso = datetime.now(KYIV_TZ).isoformat()
        payload = {
            "source": self.scraper.URL,
            "last_updated": now_iso,
            "available_dates": sorted_dates,
            "schedules": trimmed_schedules,
        }

        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Write outages.json
        outages_path = self.output_dir / "outages.json"
        with outages_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")

        # Write latest.json for quick access to the most recent date
        latest_date = sorted_dates[0]
        latest_schedule = trimmed_schedules[latest_date]
        latest_payload = {
            "source": self.scraper.URL,
            "last_updated": now_iso,
            "date": latest_schedule["date"],
            "operational_message": latest_schedule["operational_message"],
            "queues": latest_schedule["queues"],
        }
        with latest_path.open("w", encoding="utf-8") as f:
            json.dump(latest_payload, f, ensure_ascii=False, indent=2)
            f.write("\n")

        logger.info(
            f"Exported {len(sorted_dates)} date(s) to {outages_path} and {latest_path}"
        )
        return payload

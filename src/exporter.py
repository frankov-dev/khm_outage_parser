"""
JSON exporter for Khmelnytskyi power outage schedules.

Orchestrates scraping from hoe.com.ua, parsing schedule blocks, preserving
same-day revision history (`previous_schedules`), and saving structured JSON files
ready to be served via GitHub Pages or raw GitHub URLs.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from src.parser import ALL_SUBQUEUES, Parser
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
        - outages.json: Full dataset containing recent dates, queues, and previous revisions
        - latest.json: Convenience file with the most recent schedule date
        - today.json: Convenience file strictly for Kyiv's current date (if available)
        - tomorrow.json: Convenience file for Kyiv's tomorrow date (with `available: true/false`)
        - status.json: Lightweight (~250B) metadata & hash for fast Android background polling
        - schedules/<YYYY-MM-DD>.json: Individual per-date schedule files
    """

    def __init__(
        self,
        output_dir: Path | str = "docs",
        scraper: Optional[Scraper] = None,
        parser: Optional[Parser] = None,
        max_days_history: int = 14,
        check_news_for_updates: bool = True,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.scraper = scraper or Scraper()
        self.parser = parser or Parser()
        self.max_days_history = max_days_history
        self.check_news_for_updates = check_news_for_updates

    def _load_json_file(self, filename: str) -> Optional[dict[str, Any]]:
        """Load a JSON dict from output_dir if it exists."""
        file_path = self.output_dir / filename
        if not file_path.exists():
            return None
        try:
            with file_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Could not read existing {file_path}: {e}")
        return None

    def _load_existing_payload(self) -> Optional[dict[str, Any]]:
        """Load previously saved payload from outages.json if available."""
        data = self._load_json_file("outages.json")
        if data is not None and isinstance(data.get("schedules"), dict):
            return data
        return None

    def _load_existing_schedules(self) -> dict[str, Any]:
        """Load previously saved schedules to preserve history across runs."""
        payload = self._load_existing_payload()
        if payload is None:
            return {}
        return payload["schedules"]

    @staticmethod
    def _format_queues(raw_queues: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
        """Format parsed queues dict with intervals and calculated total_hours_off."""
        formatted_queues: dict[str, Any] = {}
        for queue_id in sorted(raw_queues.keys()):
            intervals = raw_queues[queue_id]
            formatted_queues[queue_id] = {
                "intervals": intervals,
                "total_hours_off": calculate_total_hours(intervals),
            }
        return formatted_queues

    @staticmethod
    def _extract_raw_queues(formatted_queues: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
        """Convert formatted queues dict ({q: {intervals, total_hours_off}}) back to raw intervals dict."""
        raw: dict[str, list[dict[str, str]]] = {}
        for q, q_data in formatted_queues.items():
            if isinstance(q_data, dict) and isinstance(q_data.get("intervals"), list):
                raw[q] = [i.copy() for i in q_data["intervals"]]
        return raw

    def _apply_version_to_base(
        self,
        base_raw: Optional[dict[str, list[dict[str, str]]]],
        ver: dict[str, Any],
    ) -> dict[str, list[dict[str, str]]]:
        """
        Apply a chronological schedule version on top of a previous state for the same date.

        Handles 3 hoe.com.ua publication patterns:
          1. Additive / delta update ("Додатково години відсутності електроенергії" or < 6 queues):
             keeps all unmentioned queues untouched and merges new intervals into mentioned queues.
          2. Tail-only mid-day update ("оновлено з HH:MM" where no interval ends before HH:MM):
             preserves already-finished intervals (end <= HH:MM, non-overlapping) and replaces
             the rest of the day with the updated intervals.
          3. Full-day schedule:
             replaces the day's schedule completely.
        """
        ver_raw: dict[str, list[dict[str, str]]] = ver["raw_queues"]
        mentioned: list[str] = ver.get("mentioned_queues", [])
        is_additive: bool = ver.get("is_additive", False)
        effective_from: Optional[str] = ver.get("effective_from")

        if base_raw is None:
            return {q: [i.copy() for i in ints] for q, ints in ver_raw.items()}

        # 1. Additive / delta update (e.g. "Додатково години відсутності електроенергії: 5.2, 6.2")
        base_active_queues = sum(1 for ints in base_raw.values() if ints)
        if is_additive or (0 < len(mentioned) < 6 and base_active_queues >= 6):
            merged: dict[str, list[dict[str, str]]] = {
                q: [i.copy() for i in base_raw.get(q, [])] for q in ALL_SUBQUEUES
            }
            for q in mentioned:
                combined = merged.get(q, []) + ver_raw.get(q, [])
                merged[q] = self.parser.merge_intervals_list(combined)
            return merged

        # 2. Tail-only mid-day update (e.g. "оновлено з 20:00" with only evening hours listed)
        if effective_from:
            cutoff_mins = self.parser.to_minutes(effective_from)
            has_earlier_intervals = any(
                self.parser.to_minutes(interval["end"]) < cutoff_mins
                for ints in ver_raw.values()
                for interval in ints
            )
            if cutoff_mins > 0 and not has_earlier_intervals:
                merged = {}
                for q in ALL_SUBQUEUES:
                    new_ints = [i.copy() for i in ver_raw.get(q, [])]
                    past_ints = [
                        i.copy()
                        for i in base_raw.get(q, [])
                        if self.parser.to_minutes(i["end"]) <= cutoff_mins
                        and not any(self.parser.overlaps(i, n) for n in new_ints)
                    ]
                    merged[q] = self.parser.merge_intervals_list(past_ints + new_ints)
                return merged

        # 3. Full-day replacement
        return {q: [i.copy() for i in ver_raw.get(q, [])] for q in ALL_SUBQUEUES}

    @staticmethod
    def _build_revision_entry(schedule_obj: dict[str, Any]) -> dict[str, Any]:
        """Create a compact revision snapshot (message + queues)."""
        return {
            "operational_message": schedule_obj.get("operational_message"),
            "queues": schedule_obj.get("queues", {}),
        }

    @staticmethod
    def _compute_schedules_hash(schedules: dict[str, Any]) -> str:
        """Compute deterministic SHA-256 prefix hash of schedules content."""
        serialized = json.dumps(schedules, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        """Write JSON payload atomically with consistent formatting."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")

    def run(self) -> Optional[dict[str, Any]]:
        """
        Fetch latest schedules, parse them, merge with history (preserving same-day
        previous_schedules), and save JSON files.

        Returns:
            The exported payload dict, or None if no schedules could be extracted.
        """
        now_kyiv = datetime.now(KYIV_TZ)
        today_str = now_kyiv.strftime("%Y-%m-%d")
        tomorrow_str = (now_kyiv + timedelta(days=1)).strftime("%Y-%m-%d")

        html = self.scraper.fetch()
        blocks = self.scraper.extract_blocks(html) if html else []

        if not blocks:
            logger.info("No schedule blocks found on main page; trying news fallback")
            fallback_html = self.scraper.fetch_latest_from_news()
            if fallback_html:
                blocks = self.scraper.extract_blocks(fallback_html)
        elif self.check_news_for_updates:
            # If the main page does not yet have tomorrow's schedule, check the latest news post
            # in case Khmelnytskoblenergo published tomorrow's forecast in News first
            main_dates = {b.get("date") for b in blocks if b.get("date")}
            if tomorrow_str not in main_dates:
                news_blocks = self.scraper.fetch_recent_news_blocks(max_posts=1)
                for nb in news_blocks:
                    if nb.get("date") not in main_dates:
                        logger.info(f"Found new date {nb.get('date')} in News not yet on main page")
                        blocks.append(nb)

        if not blocks:
            logger.warning("No schedule blocks could be extracted from hoe.com.ua")
            return None

        existing_payload = self._load_existing_payload()
        existing_schedules: dict[str, Any] = (
            existing_payload["schedules"] if existing_payload is not None else {}
        )
        schedules = dict(existing_schedules)

        # Group all extracted blocks by date in page order (newest appears first on hoe.com.ua)
        blocks_by_date: dict[str, list[dict[str, Any]]] = {}
        for block in blocks:
            parsed = self.parser.parse_block(block)
            date_str = parsed.get("date")
            raw_queues = parsed.get("queues")

            if not date_str or not raw_queues:
                continue

            blocks_by_date.setdefault(date_str, []).append({
                "date": date_str,
                "operational_message": parsed.get("message"),
                "raw_queues": raw_queues,
                "mentioned_queues": parsed.get("mentioned_queues", []),
                "is_additive": parsed.get("is_additive", False),
                "effective_from": parsed.get("effective_from"),
            })

        if not blocks_by_date:
            logger.warning("Blocks were found, but no valid queues could be parsed")
            return None

        updated_dates: list[str] = []

        for date_str, date_versions in blocks_by_date.items():
            # Replay versions chronologically (oldest -> newest) so additive/delta and
            # tail-only updates merge cleanly on top of earlier full schedules for the same day
            chronological = list(reversed(date_versions))
            old_saved = existing_schedules.get(date_str)

            base_raw: Optional[dict[str, list[dict[str, str]]]] = None
            first_ver = chronological[0]
            old_saved_active_queues = (
                sum(
                    1
                    for q_data in old_saved.get("queues", {}).values()
                    if isinstance(q_data, dict) and q_data.get("intervals")
                )
                if isinstance(old_saved, dict) and isinstance(old_saved.get("queues"), dict)
                else 0
            )
            first_is_partial = first_ver.get("is_additive", False) or (
                0 < len(first_ver.get("mentioned_queues", [])) < 6
                and old_saved_active_queues >= 6
            )

            # If the page only has a delta/partial block left, apply it on top of the previously saved schedule
            if (
                first_is_partial
                and isinstance(old_saved, dict)
                and isinstance(old_saved.get("queues"), dict)
            ):
                if old_saved.get("operational_message") == first_ver.get("operational_message"):
                    # Already applied in a previous run; avoid double-applying
                    base_raw = self._extract_raw_queues(old_saved["queues"])
                    chronological = chronological[1:]
                else:
                    base_raw = self._extract_raw_queues(old_saved["queues"])

            effective_snapshots: list[dict[str, Any]] = []
            current_raw = base_raw

            for ver in chronological:
                current_raw = self._apply_version_to_base(current_raw, ver)
                effective_snapshots.append({
                    "date": date_str,
                    "operational_message": ver.get("operational_message"),
                    "queues": self._format_queues(current_raw),
                })

            if not effective_snapshots:
                # Nothing new to apply (single delta already matched old_saved)
                if isinstance(old_saved, dict):
                    effective_snapshots.append({
                        "date": date_str,
                        "operational_message": old_saved.get("operational_message"),
                        "queues": old_saved.get("queues", {}),
                    })
                else:
                    continue

            newest = effective_snapshots[-1]
            # Order previous snapshots newest-first (excluding the active newest snapshot)
            older_on_page = [
                self._build_revision_entry(v)
                for v in reversed(effective_snapshots[:-1])
            ]

            # Combine older revisions from the current page with any previously saved revisions
            previous_revisions: list[dict[str, Any]] = []
            seen_revisions: list[dict[str, Any]] = []

            def _add_revision(rev: dict[str, Any]) -> None:
                # Skip if identical to the newest active schedule or already in the list
                if (
                    rev.get("queues") == newest["queues"]
                    and rev.get("operational_message") == newest["operational_message"]
                ):
                    return
                if any(
                    r.get("queues") == rev.get("queues")
                    and r.get("operational_message") == rev.get("operational_message")
                    for r in seen_revisions
                ):
                    return
                seen_revisions.append(rev)
                previous_revisions.append(rev)

            # 1. Revisions explicitly present on the page below the newest schedule
            for rev in older_on_page:
                _add_revision(rev)

            # 2. If there was an existing schedule saved for this date in outages.json,
            # preserve it (unless the page already provided newer+older blocks with the same message)
            # and its previous_schedules in case hoe.com.ua removed older blocks
            if isinstance(old_saved, dict):
                same_msg_recomputed = (
                    bool(older_on_page)
                    and old_saved.get("operational_message") == newest["operational_message"]
                )
                if "queues" in old_saved and not same_msg_recomputed:
                    _add_revision(self._build_revision_entry(old_saved))
                for old_rev in old_saved.get("previous_schedules", []):
                    if (
                        isinstance(old_rev, dict)
                        and "queues" in old_rev
                        and not (
                            bool(older_on_page)
                            and old_rev.get("operational_message") == newest["operational_message"]
                        )
                    ):
                        _add_revision(self._build_revision_entry(old_rev))

            schedule_entry: dict[str, Any] = {
                "date": date_str,
                "operational_message": newest["operational_message"],
                "queues": newest["queues"],
                "previous_schedules": previous_revisions,
            }

            schedules[date_str] = schedule_entry
            updated_dates.append(date_str)
            logger.info(
                f"Parsed schedule for {date_str} ({len(newest['queues'])} queues, "
                f"{len(previous_revisions)} previous revision(s))"
            )

        # Ensure all historical entries also have `previous_schedules` normalized
        for d, sched in schedules.items():
            if isinstance(sched, dict) and "previous_schedules" not in sched:
                sched["previous_schedules"] = []

        # Keep only the most recent `max_days_history` dates, sorted descending
        sorted_dates = sorted(schedules.keys(), reverse=True)[: self.max_days_history]
        trimmed_schedules = {d: schedules[d] for d in sorted_dates}

        latest_path = self.output_dir / "latest.json"
        today_path = self.output_dir / "today.json"
        tomorrow_path = self.output_dir / "tomorrow.json"
        status_path = self.output_dir / "status.json"

        # Check if midnight rollover happened (so today.json / status.json need refreshing)
        expected_today_date = today_str if today_str in trimmed_schedules else sorted_dates[0]
        existing_today = self._load_json_file("today.json")
        existing_status = self._load_json_file("status.json")
        day_files_up_to_date = (
            today_path.exists()
            and tomorrow_path.exists()
            and status_path.exists()
            and existing_today is not None
            and existing_today.get("date") == expected_today_date
            and existing_status is not None
            and existing_status.get("today_date") == (today_str if today_str in trimmed_schedules else None)
            and existing_status.get("tomorrow_date") == (tomorrow_str if tomorrow_str in trimmed_schedules else None)
        )

        if (
            existing_payload is not None
            and trimmed_schedules == existing_schedules
            and latest_path.exists()
            and day_files_up_to_date
        ):
            logger.info("Schedules unchanged; skipping JSON overwrite.")
            return existing_payload

        now_iso = now_kyiv.isoformat()
        schedule_hash = self._compute_schedules_hash(trimmed_schedules)

        payload = {
            "source": self.scraper.URL,
            "last_updated": now_iso,
            "schedule_hash": schedule_hash,
            "available_dates": sorted_dates,
            "schedules": trimmed_schedules,
        }

        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 1. Write outages.json
        outages_path = self.output_dir / "outages.json"
        self._write_json(outages_path, payload)

        # 2. Write latest.json for quick access to the most recent date
        latest_date = sorted_dates[0]
        latest_schedule = trimmed_schedules[latest_date]
        latest_payload = {
            "source": self.scraper.URL,
            "last_updated": now_iso,
            "schedule_hash": schedule_hash,
            "date": latest_schedule["date"],
            "operational_message": latest_schedule["operational_message"],
            "queues": latest_schedule["queues"],
            "previous_schedules": latest_schedule.get("previous_schedules", []),
        }
        self._write_json(latest_path, latest_payload)

        # 3. Write today.json (strictly for today's Kyiv date if available, else latest)
        today_schedule = trimmed_schedules.get(today_str, latest_schedule)
        today_payload = {
            "source": self.scraper.URL,
            "last_updated": now_iso,
            "schedule_hash": schedule_hash,
            "date": today_schedule["date"],
            "is_today": today_schedule["date"] == today_str,
            "operational_message": today_schedule["operational_message"],
            "queues": today_schedule["queues"],
            "previous_schedules": today_schedule.get("previous_schedules", []),
        }
        self._write_json(today_path, today_payload)

        # 4. Write tomorrow.json (with explicit `available` flag for the "Tomorrow" tab in Android)
        tomorrow_schedule = trimmed_schedules.get(tomorrow_str)
        if tomorrow_schedule is not None:
            tomorrow_payload = {
                "source": self.scraper.URL,
                "last_updated": now_iso,
                "schedule_hash": schedule_hash,
                "date": tomorrow_str,
                "available": True,
                "operational_message": tomorrow_schedule["operational_message"],
                "queues": tomorrow_schedule["queues"],
                "previous_schedules": tomorrow_schedule.get("previous_schedules", []),
            }
        else:
            tomorrow_payload = {
                "source": self.scraper.URL,
                "last_updated": now_iso,
                "schedule_hash": schedule_hash,
                "date": tomorrow_str,
                "available": False,
                "operational_message": None,
                "queues": {},
                "previous_schedules": [],
            }
        self._write_json(tomorrow_path, tomorrow_payload)

        # 5. Write lightweight status.json for fast mobile polling
        status_payload = {
            "source": self.scraper.URL,
            "last_updated": now_iso,
            "schedule_hash": schedule_hash,
            "latest_date": latest_date,
            "today_date": today_str if today_str in trimmed_schedules else None,
            "tomorrow_date": tomorrow_str if tomorrow_str in trimmed_schedules else None,
            "has_tomorrow": tomorrow_str in trimmed_schedules,
            "available_dates": sorted_dates,
        }
        self._write_json(status_path, status_payload)

        # 6. Write per-date archive files in docs/schedules/<YYYY-MM-DD>.json
        schedules_dir = self.output_dir / "schedules"
        for d in updated_dates:
            if d in trimmed_schedules:
                day_sched = trimmed_schedules[d]
                day_payload = {
                    "source": self.scraper.URL,
                    "last_updated": now_iso,
                    "date": day_sched["date"],
                    "operational_message": day_sched["operational_message"],
                    "queues": day_sched["queues"],
                    "previous_schedules": day_sched.get("previous_schedules", []),
                }
                self._write_json(schedules_dir / f"{d}.json", day_payload)

        logger.info(
            f"Exported {len(sorted_dates)} date(s) to {outages_path}, {latest_path}, {today_path}, {tomorrow_path}, and {status_path}"
        )
        return payload


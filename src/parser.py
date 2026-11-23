"""
Parser for power outage schedule text.

Parses schedule blocks into structured data with time intervals.
Handles operational changes (earlier start, extended end, additional outages, etc.).
"""

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

ALL_SUBQUEUES: tuple[str, ...] = (
    "1.1", "1.2",
    "2.1", "2.2",
    "3.1", "3.2",
    "4.1", "4.2",
    "5.1", "5.2",
    "6.1", "6.2",
)


class Parser:
    """
    Parser for outage schedule text.

    Handles:
        - Modern schedule: "Підчерга 1.1 – 00:00-04:00; 09:00-13:00; 18:00-21:00."
        - Explicit no-outage lines: "Підчерга 2.1 – вимкнень немає." / "без відключень"
        - Classic schedule: "підчерга 1.1 – з 10:00 до 15:00"
        - Multi-queue lines: "Підчерги 1.1, 1.2 – 00:00-04:00"
        - Earlier start: "розпочнеться раніше – о 09:00", "знеструмлена раніше – о 9:00"
        - Extended end: "триватиме довше – до 18:00", "заживлення розпочнеться о 21:00"
        - Full change / exact intervals: "відключення триватимуть з 16:00 до 17:00",
          "раніше – об 11:00 і триватиме до 16:00"
        - Extra outage: "додатково буде знеструмлено з 16:00 до 18:00"
    """

    # Time interval pattern supporting both formats:
    # 1. "з 10:00 до 15:00"
    # 2. "00:00-04:00", "09:00–13:00", "18:00 — 21:00"
    TIME_PATTERN = re.compile(
        r'(?:з\s*)?(\d{1,2}:\d{2})\s*(?:до|[-–—])\s*(\d{1,2}:\d{2})',
        re.IGNORECASE
    )

    NO_OUTAGE_PATTERN = re.compile(
        r'(?:вимкнень|відключень|знеструмлень)\s+немає|без\s+(?:вимкнень|відключень)|не\s+вимикається|скасовано',
        re.IGNORECASE
    )

    def __init__(self, ensure_all_queues: bool = True) -> None:
        self.ensure_all_queues = ensure_all_queues

    def parse_block(self, block: dict[str, Any]) -> dict[str, Any]:
        """
        Parse a schedule block for a specific date.

        Args:
            block: {"date": "YYYY-MM-DD", "schedule_text": "...", "extras_text": "..."}

        Returns:
            {"date": "...", "queues": {"1.1": [...], ...}, "message": "..."}
        """
        queues: dict[str, list[dict[str, Any]]] = {}
        explicit_no_outage_queues: set[str] = set()

        # Parse base schedule text
        schedule_text = block.get("schedule_text", "")
        for raw_line in schedule_text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue

            # Check if this line is describing subqueues
            if re.search(r'підчерг|підчерзі', line, re.IGNORECASE):
                # Extract all queues mentioned in this line (e.g. 1.1, 1.2)
                queue_matches = re.findall(r'\b([1-6]\.[1-2])\b', line)
                if not queue_matches:
                    continue

                intervals = [
                    {
                        "start": self._normalize_time(s),
                        "end": self._normalize_time(e),
                        "type": "base"
                    }
                    for s, e in self.TIME_PATTERN.findall(line)
                ]

                if intervals:
                    for queue in queue_matches:
                        queues.setdefault(queue, []).extend(intervals)
                elif self.NO_OUTAGE_PATTERN.search(line):
                    for queue in queue_matches:
                        queues.setdefault(queue, [])
                        explicit_no_outage_queues.add(queue)

        # Parse and apply operational changes
        extras_text = block.get("extras_text", "")
        if extras_text:
            self._apply_changes(extras_text, queues)

        merged_queues = self._merge_intervals(queues)
        for q in explicit_no_outage_queues:
            merged_queues.setdefault(q, [])

        # If this is a valid schedule block (has at least one parsed queue),
        # ensure all 12 standard subqueues (1.1..6.2) exist in the dictionary
        if self.ensure_all_queues and merged_queues:
            for q in ALL_SUBQUEUES:
                merged_queues.setdefault(q, [])

        return {
            "date": block["date"],
            "queues": merged_queues,
            "message": self._clean_operational_message(extras_text),
        }

    def _clean_operational_message(self, extras_text: str) -> Optional[str]:
        """Extract clean operational message without redundant subqueue change bullet lines if header exists."""
        if not extras_text or not extras_text.strip():
            return None
        return extras_text.strip()

    def _normalize_time(self, time_str: str) -> str:
        """Normalize time to HH:MM format (e.g., 7:00 -> 07:00, 24:00 -> 24:00)."""
        parts = time_str.strip().split(":")
        if len(parts) != 2:
            return time_str.strip()
        h, m = parts[0].zfill(2), parts[1].zfill(2)
        return f"{h}:{m}"

    def _apply_changes(self, extras_text: str, queues: dict[str, list[dict[str, Any]]]) -> None:
        """Apply operational changes to queue schedules."""
        # Normalize dashes and separators
        text = extras_text.replace("–", "-").replace("—", "-")

        # Split into separate entries (bullet points, sentences, semicolons)
        entries = re.split(
            r'[;\n]+|(?:\.\s+(?=у\s+підчерг|підчерг[иуа]|підчерзі))',
            text,
            flags=re.IGNORECASE,
        )
        processed: set[str] = set()

        for entry in entries:
            entry = entry.strip()
            if not entry:
                continue

            # Extract queue numbers (e.g. "у підчерг 3.2, 4.1" or "підчерга 2.1.")
            queue_nums = re.findall(r'\b([1-6]\.[1-2])\b', entry)
            if not queue_nums:
                continue

            # Pattern 1: Full change / explicit interval
            match = re.search(
                r'(?:(?:раніше|пізніше)\s*[, -]*\s*о[б]?\s*(\d{1,2}:\d{2}).+?триватиме\s+до\s*(\d{1,2}:\d{2}))|'
                r'(?:(?:триватим[еу]ть?|знеструмлено|відключення)(?:\s+довше)?\s+(?:з|о[б]?)\s*(\d{1,2}:\d{2})\s*(?:до|-)\s*(\d{1,2}:\d{2}))',
                entry,
                re.IGNORECASE
            )
            if match:
                groups = match.groups()
                if groups[0] and groups[1]:
                    start, end = self._normalize_time(groups[0]), self._normalize_time(groups[1])
                else:
                    start, end = self._normalize_time(groups[2]), self._normalize_time(groups[3])

                for q in queue_nums:
                    if f"{q}_full_{start}_{end}" not in processed:
                        queues.setdefault(q, []).append({"start": start, "end": end, "type": "change"})
                        processed.add(f"{q}_full_{start}_{end}")
                continue

            # Pattern 2: Earlier start "розпочнеться раніше - о 20:00", "почнеться раніше о 10:00", "знеструмлена раніше - о 9:00"
            match = re.search(
                r'(?:розпочнеться|почнеться|починається|знеструмлен[ао])\s+раніше\s*[, -]*\s*о[б]?\s*(\d{1,2}:\d{2})',
                entry,
                re.IGNORECASE
            )
            if match:
                new_start = self._normalize_time(match.group(1))
                for q in queue_nums:
                    key = f"{q}_start_{new_start}"
                    if key not in processed:
                        queues.setdefault(q, []).append({"start": new_start, "end": None, "type": "change_start"})
                        processed.add(key)
                continue

            # Pattern 3: Extended end "триватиме довше - до 11:00", "триватиме до 14:00", or "заживлення розпочнеться о 21:00"
            match = re.search(
                r'(?:триватиме(?:\s+довше)?\s*[, -]*\s*(?:до|заживлення.+?о[б]?)\s*(\d{1,2}:\d{2}))|'
                r'(?:заживлення(?:\s+споживачів)?\s+розпочнеться\s+о[б]?\s*(\d{1,2}:\d{2}))',
                entry,
                re.IGNORECASE
            )
            if match:
                raw_time = match.group(1) or match.group(2)
                new_end = self._normalize_time(raw_time)
                for q in queue_nums:
                    key = f"{q}_end_{new_end}"
                    if key not in processed:
                        queues.setdefault(q, []).append({"start": None, "end": new_end, "type": "change_end"})
                        processed.add(key)
                continue

            # Pattern 4: Extra outage "додатково буде знеструмлено з 16:00 до 18:00"
            match = re.search(
                r'додатково.+?з\s*(\d{1,2}:\d{2})\s*(?:до|-)\s*(\d{1,2}:\d{2})',
                entry,
                re.IGNORECASE
            )
            if match:
                start, end = self._normalize_time(match.group(1)), self._normalize_time(match.group(2))
                for q in queue_nums:
                    queues.setdefault(q, []).append({"start": start, "end": end, "type": "extra"})

    def _merge_intervals(self, queues: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, str]]]:
        """
        Merge intervals and apply changes to build final schedule.

        Applies change_start/change_end to nearest valid base intervals,
        merges overlapping intervals, returns clean result.
        """
        result: dict[str, list[dict[str, str]]] = {}

        for queue, intervals in queues.items():
            base = [i.copy() for i in intervals if i["type"] == "base" and i["start"] and i["end"]]
            changes = [i for i in intervals if i["type"] in ("change_start", "change_end")]
            full_changes = [i for i in intervals if i["type"] == "change" and i["start"] and i["end"]]
            extras = [i for i in intervals if i["type"] == "extra" and i["start"] and i["end"]]

            base.sort(key=lambda x: self._to_minutes(x["start"]))

            # Apply start/end changes to nearest valid intervals
            for change in changes:
                if not base:
                    continue
                if change["type"] == "change_start" and change["start"]:
                    idx = self._find_nearest(base, change["start"], "start")
                    if idx is not None:
                        base[idx]["start"] = change["start"]
                elif change["type"] == "change_end" and change["end"]:
                    idx = self._find_nearest(base, change["end"], "end")
                    if idx is not None:
                        base[idx]["end"] = change["end"]

            # Merge full changes with overlapping base intervals
            for fc in full_changes:
                merged_flag = False
                for bi in base:
                    if self._overlaps(bi, fc):
                        bi_s, bi_e = self._to_minutes(bi["start"]), self._to_minutes(bi["end"])
                        fc_s, fc_e = self._to_minutes(fc["start"]), self._to_minutes(fc["end"])
                        new_s = bi["start"] if bi_s <= fc_s else fc["start"]
                        new_e = bi["end"] if bi_e >= fc_e else fc["end"]
                        bi["start"] = new_s
                        bi["end"] = new_e
                        merged_flag = True
                        break
                if not merged_flag:
                    base.append({"start": fc["start"], "end": fc["end"], "type": "base"})

            # Add extras
            base.extend({"start": e["start"], "end": e["end"], "type": "base"} for e in extras)

            # Filter out any invalid intervals where start >= end
            valid_base = [
                i for i in base
                if self._to_minutes(i["start"]) < self._to_minutes(i["end"])
            ]

            # Merge overlapping and sort
            merged = self._merge_overlapping(valid_base)
            if merged:
                result[queue] = [{"start": i["start"], "end": i["end"], "type": "base"} for i in merged]

        return result

    def _find_nearest(self, intervals: list[dict[str, Any]], time: str, field: str) -> Optional[int]:
        """
        Find index of interval with nearest time value that keeps start < end valid.

        For field == 'start' (earlier start): prefers intervals starting at or after `time` (or containing `time`)
        where `end > time`.
        For field == 'end' (extended end): prefers intervals ending at or before `time` (or containing `time`)
        where `start < time`.
        """
        if not intervals:
            return None

        time_mins = self._to_minutes(time)
        best_idx: Optional[int] = None
        best_score = float("inf")

        for i, interval in enumerate(intervals):
            s_mins = self._to_minutes(interval["start"])
            e_mins = self._to_minutes(interval["end"])

            # Guard against creating invalid interval (start >= end)
            if field == "start" and time_mins >= e_mins:
                continue
            if field == "end" and time_mins <= s_mins:
                continue

            target_mins = s_mins if field == "start" else e_mins
            diff = abs(target_mins - time_mins)

            # Prefer natural direction:
            # - "earlier start" means new_start <= old_start
            # - "extended end" means new_end >= old_end
            if field == "start" and time_mins > s_mins:
                diff += 1000
            elif field == "end" and time_mins < e_mins:
                diff += 1000

            if diff < best_score:
                best_idx, best_score = i, diff

        return best_idx

    def _to_minutes(self, time_str: str) -> int:
        """
        Convert HH:MM to minutes from midnight.
        Supports 24:00 (end of day) -> 1440.
        """
        try:
            parts = time_str.split(":")
            h, m = int(parts[0]), int(parts[1])
            return h * 60 + m
        except (ValueError, IndexError, AttributeError):
            return 0

    def _overlaps(self, i1: dict[str, str], i2: dict[str, str]) -> bool:
        """Check if two intervals overlap."""
        s1, e1 = self._to_minutes(i1["start"]), self._to_minutes(i1["end"])
        s2, e2 = self._to_minutes(i2["start"]), self._to_minutes(i2["end"])
        return not (e1 <= s2 or e2 <= s1)

    def _merge_overlapping(self, intervals: list[dict[str, str]]) -> list[dict[str, str]]:
        """Merge overlapping or contiguous intervals and sort chronologically."""
        if not intervals:
            return []

        sorted_ints = sorted(intervals, key=lambda x: self._to_minutes(x["start"]))
        merged: list[dict[str, str]] = [sorted_ints[0].copy()]

        for current in sorted_ints[1:]:
            last = merged[-1]
            last_end = self._to_minutes(last["end"])
            curr_start = self._to_minutes(current["start"])
            curr_end = self._to_minutes(current["end"])

            if curr_start <= last_end:
                if curr_end > last_end:
                    last["end"] = current["end"]
            else:
                merged.append(current.copy())

        return merged


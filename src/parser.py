"""
Parser for power outage schedule text.

Parses schedule blocks into structured data with time intervals.
Handles operational changes (earlier start, extended end, additional outages, etc.).
"""

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Parser:
    """
    Parser for outage schedule text.

    Handles:
        - Modern schedule: "Підчерга 1.1 – 00:00-04:00; 09:00-13:00; 18:00-21:00."
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

    def parse_block(self, block: dict[str, Any]) -> dict[str, Any]:
        """
        Parse a schedule block for a specific date.

        Args:
            block: {"date": "YYYY-MM-DD", "schedule_text": "...", "extras_text": "..."}

        Returns:
            {"date": "...", "queues": {"1.1": [...], ...}, "message": "..."}
        """
        queues: dict[str, list[dict[str, Any]]] = {}

        # Parse base schedule text
        schedule_text = block.get("schedule_text", "")
        for raw_line in schedule_text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue

            # Check if this line is describing subqueues
            if re.search(r'підчерг', line, re.IGNORECASE):
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

        # Parse and apply operational changes
        extras_text = block.get("extras_text", "")
        if extras_text:
            self._apply_changes(extras_text, queues)

        return {
            "date": block["date"],
            "queues": self._merge_intervals(queues),
            "message": extras_text.strip() if extras_text and extras_text.strip() else None
        }

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
        entries = re.split(r'[;\n]+|(?:\.\s+(?=у\s+підчерг|підчерг[иуа]))', text)
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
                r'(?:раніше\s*-?\s*о[б]?\s*(\d{1,2}:\d{2}).+?триватиме\s+до\s*(\d{1,2}:\d{2}))|'
                r'(?:(?:триватим[еу]ть?|знеструмлено|відключення)\s+(?:з|о[б]?)\s*(\d{1,2}:\d{2})\s*(?:до|-)\s*(\d{1,2}:\d{2}))',
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

            # Pattern 2: Earlier start "розпочнеться раніше - о 20:00" or "знеструмлена раніше - о 9:00"
            match = re.search(
                r'(?:розпочнеться|знеструмлена)\s+раніше\s*-?\s*о[б]?\s*(\d{1,2}:\d{2})',
                entry,
                re.IGNORECASE
            )
            if match:
                new_start = self._normalize_time(match.group(1))
                for q in queue_nums:
                    if f"{q}_start" not in processed and f"{q}_full" not in processed:
                        queues.setdefault(q, []).append({"start": new_start, "end": None, "type": "change_start"})
                        processed.add(f"{q}_start")
                continue

            # Pattern 3: Extended end "триватиме довше - до 11:00" or "заживлення розпочнеться о 21:00"
            match = re.search(
                r'(?:триватиме\s+довше\s*-?\s*(?:до|заживлення.+?о[б]?)\s*(\d{1,2}:\d{2}))|'
                r'(?:заживлення(?:\s+споживачів)?\s+розпочнеться\s+о[б]?\s*(\d{1,2}:\d{2}))',
                entry,
                re.IGNORECASE
            )
            if match:
                raw_time = match.group(1) or match.group(2)
                new_end = self._normalize_time(raw_time)
                for q in queue_nums:
                    if f"{q}_end" not in processed:
                        queues.setdefault(q, []).append({"start": None, "end": new_end, "type": "change_end"})
                        processed.add(f"{q}_end")
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

        Applies change_start/change_end to nearest base intervals,
        merges overlapping intervals, returns clean result.
        """
        result: dict[str, list[dict[str, str]]] = {}

        for queue, intervals in queues.items():
            base = [i for i in intervals if i["type"] == "base" and i["start"] and i["end"]]
            changes = [i for i in intervals if i["type"] in ("change_start", "change_end")]
            full_changes = [i for i in intervals if i["type"] == "change" and i["start"] and i["end"]]
            extras = [i for i in intervals if i["type"] == "extra" and i["start"] and i["end"]]

            base.sort(key=lambda x: self._to_minutes(x["start"]))

            # Apply start/end changes to nearest intervals
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
                merged = False
                for bi in base:
                    if self._overlaps(bi, fc):
                        bi_s, bi_e = self._to_minutes(bi["start"]), self._to_minutes(bi["end"])
                        fc_s, fc_e = self._to_minutes(fc["start"]), self._to_minutes(fc["end"])
                        new_s = bi["start"] if bi_s <= fc_s else fc["start"]
                        new_e = bi["end"] if bi_e >= fc_e else fc["end"]
                        bi["start"] = new_s
                        bi["end"] = new_e
                        merged = True
                        break
                if not merged:
                    base.append({"start": fc["start"], "end": fc["end"], "type": "base"})

            # Add extras
            base.extend({"start": e["start"], "end": e["end"], "type": "base"} for e in extras)

            # Merge overlapping and sort
            merged = self._merge_overlapping(base)
            if merged:
                result[queue] = [{"start": i["start"], "end": i["end"], "type": "base"} for i in merged]

        return result

    def _find_nearest(self, intervals: list[dict[str, Any]], time: str, field: str) -> Optional[int]:
        """Find index of interval with nearest time value."""
        if not intervals:
            return None

        time_mins = self._to_minutes(time)
        best_idx, best_diff = 0, float("inf")

        for i, interval in enumerate(intervals):
            diff = abs(self._to_minutes(interval[field]) - time_mins)
            if diff < best_diff:
                best_idx, best_diff = i, diff

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

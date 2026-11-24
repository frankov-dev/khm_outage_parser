"""
Unit tests for outage schedule Parser.
"""

import pytest
from src.parser import ALL_SUBQUEUES, Parser


@pytest.fixture
def parser():
    return Parser()


class TestParserModernFormat:
    """Tests for modern semicolon-separated format (00:00-04:00; 09:00-13:00)."""

    def test_parse_modern_lines(self, parser):
        text = """
        За розпорядженням НЕК «Укренерго» 07 листопада на Хмельниччині будуть застосовані графіки:
        Підчерга 1.1 – 00:00-04:00; 09:00-13:00; 18:00-21:00.
        Підчерга 2.1 – 00:00-04:00; 13:00-17:00; 22:00-24:00.
        """
        block = {
            "date": "2026-11-07",
            "schedule_text": text,
            "extras_text": ""
        }
        res = parser.parse_block(block)
        assert res["date"] == "2026-11-07"
        assert set(res["queues"].keys()) == set(ALL_SUBQUEUES)

        q1 = res["queues"]["1.1"]
        assert len(q1) == 3
        assert q1[0] == {"start": "00:00", "end": "04:00", "type": "base"}
        assert q1[1] == {"start": "09:00", "end": "13:00", "type": "base"}
        assert q1[2] == {"start": "18:00", "end": "21:00", "type": "base"}

        q2 = res["queues"]["2.1"]
        assert len(q2) == 3
        assert q2[2] == {"start": "22:00", "end": "24:00", "type": "base"}

        # Unmentioned subqueues default to empty list (no outages)
        assert res["queues"]["1.2"] == []

    def test_parse_explicit_no_outages_line(self, parser):
        text = """
        Підчерга 1.1 – 21:00-24:00.
        Підчерга 2.1 – вимкнень немає.
        Підчерга 4.1 – без відключень.
        """
        block = {
            "date": "2026-11-20",
            "schedule_text": text,
            "extras_text": ""
        }
        res = parser.parse_block(block)
        assert res["queues"]["1.1"] == [{"start": "21:00", "end": "24:00", "type": "base"}]
        assert res["queues"]["2.1"] == []
        assert res["queues"]["4.1"] == []
        assert len(res["queues"]) == 12

    def test_parse_multi_queue_line(self, parser):
        text = "Підчерги 3.1, 3.2 – 04:00-08:00; 13:00-18:00."
        block = {
            "date": "2026-11-07",
            "schedule_text": text,
            "extras_text": ""
        }
        res = parser.parse_block(block)
        assert "3.1" in res["queues"]
        assert "3.2" in res["queues"]
        assert len(res["queues"]["3.1"]) == 2
        assert len(res["queues"]["3.2"]) == 2


class TestParserClassicFormat:
    """Tests for classic format (з 10:00 до 15:00)."""

    def test_parse_classic_single_interval(self, parser):
        text = "підчерга 1.1 – з 10:00 до 15:00"
        block = {
            "date": "2026-01-15",
            "schedule_text": text,
            "extras_text": ""
        }
        res = parser.parse_block(block)
        assert len(res["queues"]["1.1"]) == 1
        assert res["queues"]["1.1"][0] == {"start": "10:00", "end": "15:00", "type": "base"}

    def test_parse_classic_multiple_intervals(self, parser):
        text = "підчерга 2.1 – з 06:00 до 11:00 та з 18:00 до 23:00"
        block = {
            "date": "2026-01-15",
            "schedule_text": text,
            "extras_text": ""
        }
        res = parser.parse_block(block)
        assert len(res["queues"]["2.1"]) == 2
        assert res["queues"]["2.1"][0]["start"] == "06:00"
        assert res["queues"]["2.1"][1]["start"] == "18:00"


class TestParserOperationalChanges:
    """Tests for applying operational changes."""

    def test_earlier_start(self, parser):
        base_text = "підчерга 3.1 – 08:00-13:00; 21:00-24:00"
        extras = "- у підчерги 3.1 відключення розпочнеться раніше – о 20:00"
        block = {
            "date": "2026-11-07",
            "schedule_text": base_text,
            "extras_text": extras
        }
        res = parser.parse_block(block)
        q31 = res["queues"]["3.1"]
        assert q31[1]["start"] == "20:00"
        assert q31[1]["end"] == "24:00"

    def test_earlier_start_never_corrupts_earlier_completed_interval(self, parser):
        # 12:00 is equidistant (4h) from 08:00 and 16:00, but 08:00-11:00 ends at 11:00 < 12:00
        base_text = "підчерга 3.1 – 08:00-11:00; 16:00-20:00"
        extras = "- у підчерги 3.1 відключення почнеться раніше, о 12:00"
        block = {
            "date": "2026-11-07",
            "schedule_text": base_text,
            "extras_text": extras
        }
        res = parser.parse_block(block)
        q31 = res["queues"]["3.1"]
        assert q31[0] == {"start": "08:00", "end": "11:00", "type": "base"}
        assert q31[1] == {"start": "12:00", "end": "20:00", "type": "base"}

    def test_extended_end(self, parser):
        base_text = "підчерга 4.1 – 04:00-08:00; 13:00-17:00"
        extras = "- у підчерги 4.1 відключення триватиме довше – до 18:00"
        block = {
            "date": "2026-11-07",
            "schedule_text": base_text,
            "extras_text": extras
        }
        res = parser.parse_block(block)
        q41 = res["queues"]["4.1"]
        assert q41[1]["start"] == "13:00"
        assert q41[1]["end"] == "18:00"

    def test_power_restoration_phrase(self, parser):
        base_text = "підчерга 6.1 – 08:00-13:00; 17:00-20:00"
        extras = "- у підчерги 6.1 триватиме довше – заживлення споживачів розпочнеться о 21:00."
        block = {
            "date": "2026-11-07",
            "schedule_text": base_text,
            "extras_text": extras
        }
        res = parser.parse_block(block)
        q61 = res["queues"]["6.1"]
        assert q61[1]["end"] == "21:00"

    def test_explicit_interval_change(self, parser):
        base_text = "підчерга 2.1 – 00:00-04:00"
        extras = "- підчерга 2.1. - відключення триватимуть з 16:00 до 17:00;"
        block = {
            "date": "2026-11-07",
            "schedule_text": base_text,
            "extras_text": extras
        }
        res = parser.parse_block(block)
        q21 = res["queues"]["2.1"]
        assert len(q21) == 2
        assert q21[1] == {"start": "16:00", "end": "17:00", "type": "base"}

    def test_extra_outage_pattern(self, parser):
        base_text = "підчерга 1.1 – 10:00-14:00"
        extras = "додатково буде знеструмлено підчергу 1.1 з 16:00 до 18:00"
        block = {
            "date": "2026-11-07",
            "schedule_text": base_text,
            "extras_text": extras
        }
        res = parser.parse_block(block)
        q11 = res["queues"]["1.1"]
        assert len(q11) == 2
        assert q11[1] == {"start": "16:00", "end": "18:00", "type": "base"}


class TestParserEdgeCases:
    """Edge cases for parser."""

    def test_overlapping_intervals_merged(self, parser):
        text = "підчерга 1.1 – 10:00-14:00; 13:00-16:00"
        block = {"date": "2026-11-07", "schedule_text": text, "extras_text": ""}
        res = parser.parse_block(block)
        q11 = res["queues"]["1.1"]
        assert len(q11) == 1
        assert q11[0] == {"start": "10:00", "end": "16:00", "type": "base"}

    def test_empty_schedule(self, parser):
        block = {"date": "2026-11-07", "schedule_text": "", "extras_text": ""}
        res = parser.parse_block(block)
        assert res["queues"] == {}
        assert res["message"] is None

    def test_additive_delta_metadata_detection(self, parser):
        block = {
            "date": "2026-11-23",
            "schedule_text": "Підчерга 5.2 – 22:00-24:00.\nПідчерга 6.2 – 22:00-24:00.",
            "extras_text": (
                "За розпорядженням НЕК «Укренерго» оновлено графік погодинних відключень (ГПВ) "
                "на 23 листопада (оновлено з 21:30).\n"
                "Додатково години відсутності електроенергії:"
            ),
        }
        res = parser.parse_block(block)
        assert res["is_additive"] is True
        assert res["effective_from"] == "21:30"
        assert res["mentioned_queues"] == ["5.2", "6.2"]
        assert res["queues"]["5.2"] == [{"start": "22:00", "end": "24:00", "type": "base"}]
        assert res["queues"]["6.2"] == [{"start": "22:00", "end": "24:00", "type": "base"}]

    def test_midnight_00_00_end_normalized_to_24_00(self, parser):
        block = {
            "date": "2026-11-24",
            "schedule_text": (
                "Підчерга 2.2 – 00:00-01:00.\n"
                "Підчерга 4.2 – 22:00-00:00.\n"
                "Підчерга 5.1 – 18:00-21:00."
            ),
            "extras_text": (
                "- у підчерги 5.1 відключення триватиме довше – до 00:00\n"
                "- Підчерга 6.1 – додатково 21:00-00:00"
            ),
        }
        res = parser.parse_block(block)
        assert res["queues"]["2.2"] == [{"start": "00:00", "end": "01:00", "type": "base"}]
        assert res["queues"]["4.2"] == [{"start": "22:00", "end": "24:00", "type": "base"}]
        assert res["queues"]["5.1"] == [{"start": "18:00", "end": "24:00", "type": "base"}]
        assert res["queues"]["6.1"] == [{"start": "21:00", "end": "24:00", "type": "base"}]

    def test_cancellation_in_operational_changes(self, parser):
        block = {
            "date": "2026-11-24",
            "schedule_text": (
                "Підчерга 1.1 – 10:00-14:00; 18:00-22:00.\n"
                "Підчерга 2.1 – 12:00-16:00."
            ),
            "extras_text": (
                "- у підчерги 1.1 відключення з 18:00 до 20:00 скасовано;\n"
                "- у підчерги 2.1 відключення скасовано."
            ),
        }
        res = parser.parse_block(block)
        assert res["queues"]["1.1"] == [
            {"start": "10:00", "end": "14:00", "type": "base"},
            {"start": "20:00", "end": "22:00", "type": "base"},
        ]
        assert res["queues"]["2.1"] == []
        assert "2.1" in res["mentioned_queues"]

    def test_effective_from_requires_preposition_z(self, parser):
        # Publication timestamps without 'з' must NOT be treated as effective_from
        for pub_note in (
            "оновлено графік на 24 листопада (оновлено 23:20, 23.11.2026).",
            "оновлено графік на 16 листопада (оновлено о 23:00, 15.11.2026).",
            "оновлено графік на 21 листопада (оновлено 23:00).",
        ):
            res = parser.parse_block({
                "date": "2026-11-24",
                "schedule_text": "Підчерга 1.1 – 10:00-14:00.",
                "extras_text": pub_note,
            })
            assert res["effective_from"] is None

        # Genuine effective-from notes with 'з' MUST be detected
        for eff_note, expected in (
            ("оновлено графік на 24 листопада (оновлено з 20:00).", "20:00"),
            ("оновлено графік на 15 листопада (з 17:30).", "17:30"),
            ("Прогноз ГПВ (ОНОВЛЕНО, з 9:30).", "09:30"),
        ):
            res = parser.parse_block({
                "date": "2026-11-24",
                "schedule_text": "Підчерга 1.1 – 10:00-14:00.",
                "extras_text": eff_note,
            })
            assert res["effective_from"] == expected


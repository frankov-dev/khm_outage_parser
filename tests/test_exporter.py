"""
Unit tests for JsonExporter.
"""

import json
from unittest.mock import MagicMock

from src.exporter import JsonExporter, calculate_total_hours
from src.parser import Parser
from src.scraper import Scraper


SAMPLE_HTML = """
<div class="post">
    <p>
      За розпорядженням НЕК «Укренерго» 07 листопада на Хмельниччині будуть застосовані графіки погодинних відключень (ГПВ):
      <br/><br/>
      Підчерга 1.1 – 00:00-04:00; 09:00-13:00.
      <br/>
      Підчерга 2.1 – 22:00-24:00.
    </p>
</div>
"""


def test_calculate_total_hours():
    intervals = [
        {"start": "00:00", "end": "04:00", "type": "base"},
        {"start": "09:30", "end": "13:00", "type": "base"},
        {"start": "22:00", "end": "24:00", "type": "base"},
    ]
    assert calculate_total_hours(intervals) == 9.5


def test_exporter_writes_outages_latest_today_status_and_per_date_json(tmp_path):
    scraper = Scraper()
    scraper.fetch = MagicMock(return_value=SAMPLE_HTML)
    scraper.fetch_recent_news_blocks = MagicMock(return_value=[])

    exporter = JsonExporter(output_dir=tmp_path, scraper=scraper, parser=Parser())
    result = exporter.run()

    assert result is not None
    assert result["available_dates"] == ["2026-11-07"]
    assert "schedule_hash" in result

    outages_file = tmp_path / "outages.json"
    latest_file = tmp_path / "latest.json"
    today_file = tmp_path / "today.json"
    tomorrow_file = tmp_path / "tomorrow.json"
    status_file = tmp_path / "status.json"
    per_date_file = tmp_path / "schedules" / "2026-11-07.json"

    assert outages_file.exists()
    assert latest_file.exists()
    assert today_file.exists()
    assert tomorrow_file.exists()
    assert status_file.exists()
    assert per_date_file.exists()

    outages_data = json.loads(outages_file.read_text(encoding="utf-8"))
    assert outages_data["available_dates"] == ["2026-11-07"]
    queues = outages_data["schedules"]["2026-11-07"]["queues"]
    assert len(queues) == 12
    assert queues["1.1"]["total_hours_off"] == 8.0
    assert len(queues["1.1"]["intervals"]) == 2
    assert queues["1.2"] == {"intervals": [], "total_hours_off": 0.0}

    latest_data = json.loads(latest_file.read_text(encoding="utf-8"))
    assert latest_data["date"] == "2026-11-07"
    assert latest_data["queues"]["2.1"]["total_hours_off"] == 2.0
    assert latest_data["previous_schedules"] == []

    tomorrow_data = json.loads(tomorrow_file.read_text(encoding="utf-8"))
    assert tomorrow_data["available"] is False
    assert tomorrow_data["queues"] == {}

    status_data = json.loads(status_file.read_text(encoding="utf-8"))
    assert status_data["latest_date"] == "2026-11-07"
    assert status_data["schedule_hash"] == result["schedule_hash"]


def test_exporter_picks_up_tomorrow_schedule_from_news_when_missing_on_main_page(tmp_path):
    scraper = Scraper()
    scraper.fetch = MagicMock(return_value=SAMPLE_HTML)
    scraper.fetch_recent_news_blocks = MagicMock(
        return_value=[
            {
                "date": "2026-11-08",
                "schedule_text": "Підчерга 1.1 – 12:00-16:00.",
                "extras_text": "Прогноз ГПВ на 8 листопада.",
            }
        ]
    )

    exporter = JsonExporter(output_dir=tmp_path, scraper=scraper, parser=Parser())
    result = exporter.run()

    assert result is not None
    assert result["available_dates"] == ["2026-11-08", "2026-11-07"]
    assert result["schedules"]["2026-11-08"]["queues"]["1.1"]["total_hours_off"] == 4.0


def test_exporter_refreshes_today_json_on_midnight_rollover(tmp_path):
    scraper = Scraper()
    scraper.fetch = MagicMock(return_value=SAMPLE_HTML)
    scraper.fetch_recent_news_blocks = MagicMock(return_value=[])

    exporter = JsonExporter(output_dir=tmp_path, scraper=scraper, parser=Parser())
    first_result = exporter.run()
    assert first_result is not None

    # Simulate stale today.json from previous day before midnight rollover
    today_file = tmp_path / "today.json"
    stale_today = json.loads(today_file.read_text(encoding="utf-8"))
    stale_today["date"] = "1999-01-01"
    today_file.write_text(json.dumps(stale_today), encoding="utf-8")

    second_result = exporter.run()
    assert second_result is not None
    refreshed_today = json.loads(today_file.read_text(encoding="utf-8"))
    assert refreshed_today["date"] == "2026-11-07"


def test_exporter_preserves_history_across_runs(tmp_path):
    existing = {
        "source": Scraper.URL,
        "last_updated": "2026-11-06T12:00:00+02:00",
        "available_dates": ["2026-11-06"],
        "schedules": {
            "2026-11-06": {
                "date": "2026-11-06",
                "operational_message": None,
                "queues": {
                    "1.1": {"intervals": [{"start": "00:00", "end": "02:00", "type": "base"}], "total_hours_off": 2.0}
                },
                "previous_schedules": [],
            }
        },
    }
    (tmp_path / "outages.json").write_text(json.dumps(existing), encoding="utf-8")

    scraper = Scraper()
    scraper.fetch = MagicMock(return_value=SAMPLE_HTML)
    scraper.fetch_recent_news_blocks = MagicMock(return_value=[])

    exporter = JsonExporter(output_dir=tmp_path, scraper=scraper, parser=Parser())
    result = exporter.run()

    assert result is not None
    assert result["available_dates"] == ["2026-11-07", "2026-11-06"]


def test_exporter_returns_none_when_no_data(tmp_path):
    scraper = Scraper()
    scraper.fetch = MagicMock(return_value=None)
    scraper.fetch_latest_from_news = MagicMock(return_value=None)

    exporter = JsonExporter(output_dir=tmp_path, scraper=scraper, parser=Parser())
    assert exporter.run() is None
    assert not (tmp_path / "outages.json").exists()


def test_exporter_keeps_newest_and_saves_older_blocks_in_previous_schedules(tmp_path):
    html = """
    <div class="post">
        <p>Графік на 07 листопада (оновлений з 11:00):</p>
        <p>Підчерга 1.1 – 10:00-14:00</p>
        <hr>
        <p>Графік на 07 листопада (початковий):</p>
        <p>Підчерга 1.1 – 00:00-02:00</p>
    </div>
    """
    scraper = Scraper()
    scraper.fetch = MagicMock(return_value=html)
    scraper.fetch_recent_news_blocks = MagicMock(return_value=[])

    exporter = JsonExporter(output_dir=tmp_path, scraper=scraper, parser=Parser())
    result = exporter.run()

    assert result is not None
    day_sched = result["schedules"]["2026-11-07"]
    q11 = day_sched["queues"]["1.1"]
    assert q11["intervals"] == [{"start": "10:00", "end": "14:00", "type": "base"}]

    prev = day_sched["previous_schedules"]
    assert len(prev) == 1
    assert prev[0]["operational_message"] == "Графік на 07 листопада (початковий):"
    assert prev[0]["queues"]["1.1"]["intervals"] == [{"start": "00:00", "end": "02:00", "type": "base"}]


def test_exporter_skips_overwrite_when_schedules_unchanged(tmp_path):
    scraper = Scraper()
    scraper.fetch = MagicMock(return_value=SAMPLE_HTML)
    scraper.fetch_recent_news_blocks = MagicMock(return_value=[])

    exporter = JsonExporter(output_dir=tmp_path, scraper=scraper, parser=Parser())
    first_result = exporter.run()
    assert first_result is not None

    outages_file = tmp_path / "outages.json"
    latest_file = tmp_path / "latest.json"
    outages_before = outages_file.read_text(encoding="utf-8")
    latest_before = latest_file.read_text(encoding="utf-8")

    second_result = exporter.run()
    assert second_result == first_result
    assert outages_file.read_text(encoding="utf-8") == outages_before
    assert latest_file.read_text(encoding="utf-8") == latest_before


def test_exporter_rewrites_when_latest_json_missing(tmp_path):
    scraper = Scraper()
    scraper.fetch = MagicMock(return_value=SAMPLE_HTML)
    scraper.fetch_recent_news_blocks = MagicMock(return_value=[])

    exporter = JsonExporter(output_dir=tmp_path, scraper=scraper, parser=Parser())
    assert exporter.run() is not None

    latest_file = tmp_path / "latest.json"
    latest_file.unlink()
    assert not latest_file.exists()

    second_result = exporter.run()
    assert second_result is not None
    assert latest_file.exists()


def test_exporter_overwrites_and_archives_previous_schedule_when_schedule_changes(tmp_path):
    scraper = Scraper()
    scraper.fetch = MagicMock(return_value=SAMPLE_HTML)
    scraper.fetch_recent_news_blocks = MagicMock(return_value=[])

    exporter = JsonExporter(output_dir=tmp_path, scraper=scraper, parser=Parser())
    first_result = exporter.run()
    assert first_result is not None

    updated_html = SAMPLE_HTML.replace("22:00-24:00", "21:00-24:00")
    scraper.fetch = MagicMock(return_value=updated_html)

    second_result = exporter.run()
    assert second_result is not None
    assert second_result["last_updated"] != first_result["last_updated"]
    day_sched = second_result["schedules"]["2026-11-07"]
    assert day_sched["queues"]["2.1"]["total_hours_off"] == 3.0
    assert len(day_sched["previous_schedules"]) == 1
    assert day_sched["previous_schedules"][0]["queues"]["2.1"]["total_hours_off"] == 2.0




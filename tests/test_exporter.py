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


def test_exporter_writes_outages_and_latest_json(tmp_path):
    scraper = Scraper()
    scraper.fetch = MagicMock(return_value=SAMPLE_HTML)

    exporter = JsonExporter(output_dir=tmp_path, scraper=scraper, parser=Parser())
    result = exporter.run()

    assert result is not None
    assert result["available_dates"] == ["2026-11-07"]

    outages_file = tmp_path / "outages.json"
    latest_file = tmp_path / "latest.json"
    assert outages_file.exists()
    assert latest_file.exists()

    outages_data = json.loads(outages_file.read_text(encoding="utf-8"))
    assert outages_data["available_dates"] == ["2026-11-07"]
    q11 = outages_data["schedules"]["2026-11-07"]["queues"]["1.1"]
    assert q11["total_hours_off"] == 8.0
    assert len(q11["intervals"]) == 2

    latest_data = json.loads(latest_file.read_text(encoding="utf-8"))
    assert latest_data["date"] == "2026-11-07"
    assert latest_data["queues"]["2.1"]["total_hours_off"] == 2.0


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
            }
        },
    }
    (tmp_path / "outages.json").write_text(json.dumps(existing), encoding="utf-8")

    scraper = Scraper()
    scraper.fetch = MagicMock(return_value=SAMPLE_HTML)

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


def test_exporter_keeps_first_newest_block_for_duplicate_date(tmp_path):
    html = """
    <div class="post">
        <p>Графік на 07 листопада (оновлений):</p>
        <p>Підчерга 1.1 – 10:00-14:00</p>
        <hr>
        <p>Графік на 07 листопада (старий):</p>
        <p>Підчерга 1.1 – 00:00-02:00</p>
    </div>
    """
    scraper = Scraper()
    scraper.fetch = MagicMock(return_value=html)

    exporter = JsonExporter(output_dir=tmp_path, scraper=scraper, parser=Parser())
    result = exporter.run()

    assert result is not None
    q11 = result["schedules"]["2026-11-07"]["queues"]["1.1"]
    assert q11["intervals"] == [{"start": "10:00", "end": "14:00", "type": "base"}]


def test_exporter_skips_overwrite_when_schedules_unchanged(tmp_path):
    scraper = Scraper()
    scraper.fetch = MagicMock(return_value=SAMPLE_HTML)

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

    exporter = JsonExporter(output_dir=tmp_path, scraper=scraper, parser=Parser())
    assert exporter.run() is not None

    latest_file = tmp_path / "latest.json"
    latest_file.unlink()
    assert not latest_file.exists()

    second_result = exporter.run()
    assert second_result is not None
    assert latest_file.exists()


def test_exporter_overwrites_when_schedule_changes(tmp_path):
    scraper = Scraper()
    scraper.fetch = MagicMock(return_value=SAMPLE_HTML)

    exporter = JsonExporter(output_dir=tmp_path, scraper=scraper, parser=Parser())
    first_result = exporter.run()
    assert first_result is not None

    updated_html = SAMPLE_HTML.replace("22:00-24:00", "21:00-24:00")
    scraper.fetch = MagicMock(return_value=updated_html)

    second_result = exporter.run()
    assert second_result is not None
    assert second_result["last_updated"] != first_result["last_updated"]
    q21 = second_result["schedules"]["2026-11-07"]["queues"]["2.1"]
    assert q21["total_hours_off"] == 3.0



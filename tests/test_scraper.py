"""
Unit tests for outage schedule Scraper.
"""

from unittest.mock import MagicMock, patch
import pytest
import requests

from src.scraper import Scraper


@pytest.fixture
def scraper():
    return Scraper()


class TestScraperDateExtraction:
    """Tests for extract_date across multiple date formats."""

    def test_extract_ukrainian_text_month(self, scraper):
        assert scraper.extract_date("розпорядженням на 07 листопада будуть") == "2026-11-07"
        assert scraper.extract_date("Графіки на 15 січня 2026 року") == "2026-01-15"
        assert scraper.extract_date("Погодинні відключення 23 березня") == "2026-03-23"

    def test_extract_numeric_dots(self, scraper):
        assert scraper.extract_date("Графік відключень 17.10.2026") == "2026-10-17"
        assert scraper.extract_date("Оновлення від 08.04.26") == "2026-04-08"

    def test_extract_image_alt(self, scraper):
        assert scraper.extract_date("ГПВ-15.01.26") == "2026-01-15"
        assert scraper.extract_date("ГПВ_30.06.2026") == "2026-06-30"
        assert scraper.extract_date("ГПВ 17102026") == "2026-10-17"

    def test_extract_date_invalid(self, scraper):
        assert scraper.extract_date("") is None
        assert scraper.extract_date("Текст без дат") is None


class TestScraperBlockExtraction:
    """Tests for extract_blocks on various HTML structures."""

    def test_modern_html_extraction(self, scraper):
        html = """
        <div class="post">
            <p>
              За розпорядженням НЕК «Укренерго» 07 листопада на Хмельниччині будуть застосовані графіки погодинних відключень (ГПВ):
              <br/><br/>
              Підчерга 1.1 – 00:00-04:00; 09:00-13:00; 18:00-21:00.
              <br/>
              Підчерга 1.2 – 00:00-04:00; 09:00-13:00; 18:00-21:00.
            </p>
            <hr>
            <p>Перевірити чергу</p>
        </div>
        """
        blocks = scraper.extract_blocks(html)
        assert len(blocks) == 1
        assert blocks[0]["date"] == "2026-11-07"
        assert "Підчерга 1.1" in blocks[0]["schedule_text"]
        assert "Підчерга 1.2" in blocks[0]["schedule_text"]
        assert "За розпорядженням НЕК «Укренерго»" in blocks[0]["extras_text"]

    def test_legacy_html_with_image_alt(self, scraper):
        html = """
        <div class="post">
            <p>Оперативне повідомлення: відключення раніше о 20:00</p>
            <img alt="ГПВ-20.01.26">
            <ul>
                <li>підчерга 1.1 – з 10:00 до 15:00;</li>
            </ul>
        </div>
        """
        blocks = scraper.extract_blocks(html)
        assert len(blocks) == 1
        assert blocks[0]["date"] == "2026-01-20"
        assert "підчерга 1.1" in blocks[0]["schedule_text"]
        assert "Оперативне повідомлення" in blocks[0]["extras_text"]

    def test_multi_day_html(self, scraper):
        html = """
        <div class="post">
            <p>Графік на 07 листопада:</p>
            <p>Підчерга 1.1 – 00:00-04:00</p>
            <hr>
            <p>Графік на 08 листопада:</p>
            <p>Підчерга 1.1 – 04:00-08:00</p>
        </div>
        """
        blocks = scraper.extract_blocks(html)
        assert len(blocks) == 2
        assert blocks[0]["date"] == "2026-11-07"
        assert blocks[1]["date"] == "2026-11-08"

    def test_empty_or_missing_post(self, scraper):
        assert scraper.extract_blocks("<html><body><div>Немає розкладу</div></body></html>") == []


class TestScraperNetwork:
    """Tests for fetch error handling and retries."""

    def test_fetch_success(self, scraper):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = "<html>ok</html>"
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            html = scraper.fetch("https://hoe.com.ua")
            assert html == "<html>ok</html>"
            assert mock_get.call_count == 1

    def test_fetch_retry_then_succeed(self, scraper):
        with patch("requests.get") as mock_get:
            mock_fail = MagicMock()
            mock_fail.raise_for_status.side_effect = requests.RequestException("Network error")

            mock_ok = MagicMock()
            mock_ok.text = "<html>success</html>"
            mock_ok.raise_for_status = MagicMock()

            mock_get.side_effect = [mock_fail, mock_ok]

            html = scraper.fetch("https://hoe.com.ua")
            assert html == "<html>success</html>"
            assert mock_get.call_count == 2

    def test_fetch_all_retries_fail(self, scraper):
        with patch("requests.get", side_effect=requests.RequestException("Timeout")):
            html = scraper.fetch("https://hoe.com.ua")
            assert html is None

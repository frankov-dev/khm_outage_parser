"""
Unit tests for outage schedule Scraper.
"""

from unittest.mock import MagicMock, patch
import pytest
import requests

from src.scraper import Scraper


@pytest.fixture
def scraper():
    s = Scraper()
    s.RETRY_BACKOFF_SECONDS = 0
    return s


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

    def test_extract_date_ignores_parenthetical_update_timestamp(self, scraper):
        text = "За розпорядженням НЕК «Укренерго» оновлено графік на 16 листопада (оновлено о 23:00, 15.11.2026)."
        assert scraper.extract_date(text) == "2026-11-16"

    def test_extract_date_corrects_editorial_year_typo(self, scraper):
        # Real hoe.com.ua typo from Oct 17, 2026: alt="ГПВ 17102027 оновлено 2"
        assert scraper.extract_date("ГПВ 17102027 оновлено 2") == "2026-10-17"

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

    def test_splits_multiple_updates_without_hr_separator(self, scraper):
        html = """
        <div class="post">
            <p>За розпорядженням НЕК «Укренерго» оновлено графік погодинних відключень (ГПВ) на 15 листопада (з 17:30).</p>
            <p>Підчерга 1.1 – 19:00-22:00.</p>
            <p>За розпорядженням НЕК «Укренерго» 15.11.2026 р. на Хмельниччині будуть застосовані графіки погодинних відключень (ГПВ).</p>
            <p>Підчерга 1.1 – 16:00-19:00.</p>
        </div>
        """
        blocks = scraper.extract_blocks(html)
        assert len(blocks) == 2
        assert "19:00-22:00" in blocks[0]["schedule_text"]
        assert "16:00-19:00" in blocks[1]["schedule_text"]

    def test_strips_boilerplate_disclaimers_from_extras(self, scraper):
        html = """
        <div class="post">
            <p>За розпорядженням НЕК «Укренерго» 18.11.2026 р. будуть застосовані графіки погодинних відключень (ГПВ).</p>
            <p>Підчерга 1.1 – 06:00-10:00.</p>
            <p>Для промислових споживачів діятиме графік обмеження споживання електричної потужності.</p>
            <p>Наголошуємо, що головною причиною запровадження графіків обмеження та відключення електропостачання є ракетно-дронові обстріли з боку ворога.</p>
            <p>Зверніть увагу, це прогноз відключень.</p>
            <p>Перевірити свою чергу ГПВ можна на наших ресурсах:</p>
        </div>
        """
        blocks = scraper.extract_blocks(html)
        assert len(blocks) == 1
        assert "Наголошуємо" not in blocks[0]["extras_text"]
        assert "Для промислових" not in blocks[0]["extras_text"]
        assert "Перевірити свою чергу" not in blocks[0]["extras_text"]
        assert "За розпорядженням" in blocks[0]["extras_text"]

    def test_keeps_plural_additive_header_and_collapses_nbsp_spaces(self, scraper):
        html = """
        <div class="post">
            <p>За розпорядженням НЕК «Укренерго» оновлено графік погодинних відключень (ГПВ) на 24 листопада (оновлено з\xa0 20:00).</p>
            <p>Додаткові\xa0години відсутності електроенергії:</p>
            <p>Підчерга 2.1 - 20:00-22:00.</p>
            <p>Підчерга 4.2\xa0- 22:00-00:00.</p>
        </div>
        """
        blocks = scraper.extract_blocks(html)
        assert len(blocks) == 1
        assert "(оновлено з 20:00)" in blocks[0]["extras_text"]
        assert "Додаткові години відсутності електроенергії:" in blocks[0]["extras_text"]
        assert "Підчерга 4.2 - 22:00-00:00." in blocks[0]["schedule_text"]

    def test_find_news_schedule_links_skips_faq_and_articles(self, scraper):
        news_html = """
        <html><body>
            <a href="/post/korotko-pro-vazhlive-chi-vigidno-at-hmelnitskoblenergo-vidkljuchati-svitlo.html">Чи вигідно відключати світло?</a>
            <a href="/post/chomu-grafiki-zminjujutsjapo-kilka-raziv-na-den.html">Чому графіки змінюються по кілька разів на день?</a>
            <a href="/post/pitannjavidpovid-grafiki-pogodinnih-vidkljuchen-gpv.html">#ПитанняВідповідь. Графіки погодинних відключень (ГПВ)</a>
            <a href="/post/prognoz-gpv-na-23-listopada.html">Прогноз ГПВ на 23 листопада (ОНОВЛЕНО, з 11:00)</a>
            <a href="/post/pogodinni-vidkljuchennja-na-18-listopada.html">Погодинні відключення на 18 листопада</a>
        </body></html>
        """
        links = scraper.find_news_schedule_links(news_html)
        assert links == [
            "https://hoe.com.ua/post/prognoz-gpv-na-23-listopada.html",
            "https://hoe.com.ua/post/pogodinni-vidkljuchennja-na-18-listopada.html",
        ]

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


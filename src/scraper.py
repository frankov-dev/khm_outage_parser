"""
Web scraper for hoe.com.ua power outage schedules.

Extracts schedule blocks with dates from the official Khmelnytskoblenergo website.
Supports both modern text-based schedules (Ukrainian month names) and legacy image-based markers.
Includes retry mechanisms with backoff and smart news-section fallback.
"""

import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

KYIV_TZ = ZoneInfo("Europe/Kyiv")

UKRAINIAN_MONTHS = {
    "січня": 1,
    "лютого": 2,
    "березня": 3,
    "квітня": 4,
    "травня": 5,
    "червня": 6,
    "липня": 7,
    "серпня": 8,
    "вересня": 9,
    "жовтня": 10,
    "листопада": 11,
    "грудня": 12,
}


class Scraper:
    """
    Scraper for hoe.com.ua outage schedules page.

    Supports:
        - Modern text announcements (e.g. "07 листопада", "07.11.2026")
        - Legacy image alt date markers (e.g. "ГПВ-15.01.26", "ГПВ_30.06.2026", "ГПВ 17102026")
        - Multi-day or multi-update blocks separated by <hr> or announcement headings
        - Fallback and proactive fetching from company news
    """

    URL = "https://hoe.com.ua/page/pogodinni-vidkljuchennja"
    NEWS_URL = "https://hoe.com.ua/post/novini-kompaniji"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    TIMEOUT = 15
    MAX_RETRIES = 3
    RETRY_BACKOFF_SECONDS = 1.0

    # Regex patterns for date detection
    ALT_DATE_PATTERN = re.compile(
        r'ГПВ[-_.\s]*(\d{2})[.\s]?(\d{2})[.\s]?(\d{2,4})',
        re.IGNORECASE
    )
    MONTHS_PATTERN = re.compile(
        rf'(\d{{1,2}})\s+({"|".join(UKRAINIAN_MONTHS.keys())})(?:\s+(\d{{4}}))?',
        re.IGNORECASE
    )
    NUMERIC_DATE_PATTERN = re.compile(r'\b(\d{1,2})\.(\d{2})\.(\d{2,4})\b')

    # Pattern to strip parenthetical timestamps like "(оновлено о 23:00, 15.11.2026)" before extracting target date
    PARENTHETICAL_UPDATE_PATTERN = re.compile(r'\([^)]*оновлено[^)]*\)', re.IGNORECASE)

    # Pattern identifying a new schedule announcement header
    ANNOUNCEMENT_HEADER_PATTERN = re.compile(
        r'(?:за\s+розпорядженням|відповідно\s+до\s+розпорядження)\s+нек\s+[«"„]?укренерго',
        re.IGNORECASE,
    )

    # Pattern matching a subqueue line
    SUBQUEUE_LINE_PATTERN = re.compile(
        r'(?:підчерг[аиу]|підчерзі)\s*[№#]?\s*\d\.\d',
        re.IGNORECASE,
    )

    # Keywords indicating that a subqueue line is an operational change rather than a base schedule
    CHANGE_LINE_KEYWORDS = (
        "раніше",
        "пізніше",
        "довше",
        "триватим",
        "скасовано",
        "заживлення",
        "додатков",
        "розпочнеться",
        "почнеться",
        "починається",
        "збільшено",
        "зменшено",
    )

    # Boilerplate disclaimers to exclude from operational_message
    BOILERPLATE_PREFIXES = (
        "електроенергія у підчерг",
        "наголошуємо, що головною причиною",
        "наголошуємо: причиною запровадження",
        "наголошуємо, хмельницька область",
        "наголошуємо, проінформувати заздалегідь",
        "для промислових споживачів",
        "зверніть увагу, це прогноз",
        "зверніть увагу: одна і та ж будівля",
        "нагадаємо, перевірити свою чергу",
        "шановні споживачі! радимо переглянути",
        "якщо ви виявили, що відключення",
        "будь ласка, коли розподіл електроенергії",
        "такі заходи є вимушеними",
        "енергетики країни працюють",
        "актуальна інформація щодо погодинних",
        "графік погодинних відключень не є єдиним",
        "важливо:",
        "відділ зв",
    )

    # News post URL/title filters for actual schedule posts
    NEWS_SCHEDULE_HREF_PATTERN = re.compile(
        r'post/(?:prognoz-gpv|pogodinni-vidkljuchennja-na|prognoz-obmezhen-na|grafik-(?:pogodinnih-)?vidkljuchen-na|grafiki-obmezhen-na|uvaga-zmini-grafika)',
        re.IGNORECASE,
    )
    NEWS_EXCLUDED_HREF_KEYWORDS = (
        "chomu-",
        "pitannjavidpovid",
        "chi-vigidno",
        "jak-pratsjuje",
        "riznitsja",
        "povnij-gajd",
        "olega-ivasika",
        "v-omanu",
    )

    def fetch(self, url: Optional[str] = None) -> Optional[str]:
        """
        Fetch HTML content from website with retry and exponential backoff.

        Args:
            url: Target URL, defaults to self.URL.

        Returns:
            HTML content as string, or None if request failed.
        """
        target_url = url or self.URL
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response = requests.get(target_url, headers=self.HEADERS, timeout=self.TIMEOUT)
                response.raise_for_status()
                logger.info(f"Successfully fetched {target_url} ({len(response.text)} bytes)")
                return response.text
            except requests.RequestException as e:
                logger.warning(f"Attempt {attempt}/{self.MAX_RETRIES} failed for {target_url}: {e}")
                if attempt < self.MAX_RETRIES and self.RETRY_BACKOFF_SECONDS > 0:
                    time.sleep(self.RETRY_BACKOFF_SECONDS * attempt)

        logger.error(f"All {self.MAX_RETRIES} attempts failed for {target_url}")
        return None

    def _resolve_year(self, month: int, day: int, explicit_year: Optional[str]) -> int:
        """Resolve year accurately, handling Dec -> Jan year rollover and obvious typos."""
        now_kyiv = datetime.now(KYIV_TZ)
        current_year = now_kyiv.year

        if explicit_year:
            y = int(f"20{explicit_year}" if len(explicit_year) == 2 else explicit_year)
            # Protect against obvious editorial year typos (e.g. "2027" instead of "2026" in October)
            try:
                candidate = date(y, month, day)
                if abs((candidate - now_kyiv.date()).days) > 300:
                    corrected = date(current_year, month, day)
                    if abs((corrected - now_kyiv.date()).days) <= 60:
                        logger.warning(
                            f"Corrected probable typo year {y} -> {current_year} for {day:02d}.{month:02d}"
                        )
                        return current_year
            except ValueError:
                pass
            return y

        # When no year is specified, handle New Year boundary (e.g. Dec 31 posting for Jan 1, or Jan 1 viewing Dec 31)
        if now_kyiv.month == 12 and month == 1:
            return current_year + 1
        if now_kyiv.month == 1 and month == 12 and now_kyiv.day <= 7:
            return current_year - 1
        return current_year

    @staticmethod
    def _format_valid_date(year: int, month: int, day: int) -> Optional[str]:
        """Validate calendar date and return YYYY-MM-DD string."""
        try:
            return date(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    def extract_date(self, text: str) -> Optional[str]:
        """
        Extract date in YYYY-MM-DD format from text or image alt attribute.

        Supports:
            1. Image alt: 'ГПВ-15.01.26', 'ГПВ_30.06.2026', 'ГПВ 17102026'
            2. Ukrainian text: '07 листопада', '15 січня 2026'
            3. Numeric dot format: '07.11.2026', '07.11.26'
        """
        if not text:
            return None

        # Strip parenthetical update timestamps like "(оновлено о 23:00, 15.11.2026)"
        cleaned_text = self.PARENTHETICAL_UPDATE_PATTERN.sub("", text)

        # 1. Image alt match
        alt_match = self.ALT_DATE_PATTERN.search(cleaned_text)
        if alt_match:
            d, m, y = alt_match.groups()
            day, month = int(d), int(m)
            year = self._resolve_year(month, day, y)
            valid = self._format_valid_date(year, month, day)
            if valid:
                return valid

        # 2. Ukrainian month match (prefer target phrase "на DD <місяця>" if present)
        target_month_match = re.search(
            rf'\bна\s+(?:[а-яіїєґ]+\s+)?(\d{{1,2}})\s+({"|".join(UKRAINIAN_MONTHS.keys())})(?:\s+(\d{{4}}))?',
            cleaned_text,
            re.IGNORECASE,
        )
        month_match = target_month_match or self.MONTHS_PATTERN.search(cleaned_text)
        if month_match:
            d, m_name, y = month_match.groups()
            day = int(d)
            month = UKRAINIAN_MONTHS[m_name.lower()]
            year = self._resolve_year(month, day, y)
            valid = self._format_valid_date(year, month, day)
            if valid:
                return valid

        # 3. Numeric dot match
        num_match = self.NUMERIC_DATE_PATTERN.search(cleaned_text)
        if num_match:
            d, m, y = num_match.groups()
            day, month = int(d), int(m)
            year = self._resolve_year(month, day, y)
            valid = self._format_valid_date(year, month, day)
            if valid:
                return valid

        return None

    def _is_boilerplate_line(self, line: str) -> bool:
        """Check if a line is standard informational boilerplate rather than an operational message."""
        lower = re.sub(r"[ \t]+", " ", line.lower().replace("\xa0", " ")).strip()
        if (
            re.search(r"перевірити\s+(?:свою\s+)?чергу", lower)
            or "графік обмеження споживання електричної потужності" in lower
        ):
            return True
        return any(lower.startswith(prefix) for prefix in self.BOILERPLATE_PREFIXES)

    def extract_blocks(self, html: str) -> list[dict[str, Any]]:
        """
        Extract schedule blocks from HTML.

        Each block contains a date (YYYY-MM-DD), schedule_text, and extras_text.

        Args:
            html: Raw HTML content

        Returns:
            List of dicts with keys: date, schedule_text, extras_text
        """
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        content = (
            soup.find("div", class_="post")
            or soup.find("article")
            or soup.find("main")
            or soup.body
        )

        if not content:
            return []

        # Split content into sections by <hr> separators OR repeated announcement headers
        raw_children = list(content.children)
        sections: list[list[Any]] = [[]]
        current_has_subqueues = False

        for child in raw_children:
            if isinstance(child, Tag):
                if child.name == "hr":
                    sections.append([])
                    current_has_subqueues = False
                    continue

                child_text = child.get_text(" ", strip=True)
                # If a section already collected subqueue lines and we encounter a new
                # "За розпорядженням НЕК «Укренерго»..." paragraph without an <hr>, split into a new section
                if (
                    current_has_subqueues
                    and self.ANNOUNCEMENT_HEADER_PATTERN.search(child_text)
                    and not self.SUBQUEUE_LINE_PATTERN.search(child_text)
                ):
                    sections.append([])
                    current_has_subqueues = False

                sections[-1].append(child)
                if "підчерг" in child_text.lower() or "підчерзі" in child_text.lower():
                    current_has_subqueues = True
            elif isinstance(child, str) and child.strip():
                sections[-1].append(child)
                if "підчерг" in child.lower() or "підчерзі" in child.lower():
                    current_has_subqueues = True

        blocks: list[dict[str, Any]] = []

        for sec in sections:
            if not sec:
                continue

            sec_text = "\n".join(
                e.get_text("\n", strip=True) if isinstance(e, Tag) else str(e).strip()
                for e in sec
            ).replace("\xa0", " ")

            # Skip sections that do not contain outage schedules
            if "підчерг" not in sec_text.lower() and "підчерзі" not in sec_text.lower():
                continue

            # Detect date from section text first (more reliable than image alt typos)
            date_str = self.extract_date(sec_text)

            # Fallback to date-marked images if text has no explicit date
            if not date_str:
                for e in sec:
                    if isinstance(e, Tag):
                        imgs = [e] if e.name == "img" else e.find_all("img")
                        for img in imgs:
                            alt = img.get("alt", "")
                            d = self.extract_date(alt)
                            if d:
                                date_str = d
                                break
                        if date_str:
                            break

            # Fallback to Kyiv today's date if no date found
            if not date_str:
                date_str = datetime.now(KYIV_TZ).strftime("%Y-%m-%d")

            schedule_lines: list[str] = []
            extras_lines: list[str] = []
            keywords = [
                "підчерг", "підчерзі", "відключен", "знеструм", "раніше", "пізніше", "довше",
                "додатков", "збільшено", "зменшено", "оновлен", "укренерго", "розпоряджен", "триватим",
                "заживлення", "скасовано", "графік", "гпв"
            ]

            for raw_line in sec_text.split("\n"):
                line = re.sub(r"[ \t]+", " ", raw_line).strip()
                if not line:
                    continue

                if self._is_boilerplate_line(line):
                    continue

                if self.SUBQUEUE_LINE_PATTERN.search(line) or re.search(
                    r'підчерг[аиу]?\s+\d\.\d\s*[,таі]', line, re.IGNORECASE
                ):
                    # Distinguish base schedule line from operational change note
                    if any(kw in line.lower() for kw in self.CHANGE_LINE_KEYWORDS):
                        extras_lines.append(line)
                    else:
                        schedule_lines.append(line)
                elif any(kw in line.lower() for kw in keywords):
                    extras_lines.append(line)

            # If a block only contains explicit operational change intervals (e.g. "- підчерга 2.1 - відключення триватимуть з 16:00 до 17:00"),
            # keep the block so Parser can extract full intervals from extras_text.
            if schedule_lines or extras_lines:
                blocks.append({
                    "date": date_str,
                    "schedule_text": "\n".join(schedule_lines),
                    "extras_text": "\n".join(extras_lines).strip()
                })

        return blocks

    def find_news_schedule_links(self, news_html: str, limit: int = 3) -> list[str]:
        """
        Extract top schedule post URLs from the company news page HTML.

        Filters out general informational articles (FAQ, interviews, explanations)
        and prioritizes actual schedule announcements.
        """
        if not news_html:
            return []

        soup = BeautifulSoup(news_html, "html.parser")
        candidates: list[str] = []
        fallback_candidates: list[str] = []

        for a in soup.find_all("a"):
            title = a.get_text(" ", strip=True).lower()
            href = a.get("href", "").strip()
            if not href or "post/" not in href or "novini-kompaniji" in href:
                continue

            if any(excl in href.lower() for excl in self.NEWS_EXCLUDED_HREF_KEYWORDS):
                continue
            if "?" in title:
                continue

            full_url = href if href.startswith("http") else f"https://hoe.com.ua{href}"

            if self.NEWS_SCHEDULE_HREF_PATTERN.search(href):
                if full_url not in candidates:
                    candidates.append(full_url)
            elif any(w in title for w in ["погодинні відключення", "прогноз гпв", "графік погодинних", "графік відключень"]):
                if full_url not in fallback_candidates:
                    fallback_candidates.append(full_url)

        combined = candidates + [u for u in fallback_candidates if u not in candidates]
        return combined[:limit]

    def fetch_recent_news_blocks(self, max_posts: int = 2) -> list[dict[str, Any]]:
        """
        Fetch and extract schedule blocks from the most recent schedule posts in company news.

        Useful both as a fallback when the main page is empty and for catching evening
        "tomorrow" schedules posted in News before the static main page is updated.
        """
        news_html = self.fetch(self.NEWS_URL)
        if not news_html:
            return []

        links = self.find_news_schedule_links(news_html, limit=max_posts)
        collected_blocks: list[dict[str, Any]] = []

        for post_link in links:
            logger.info(f"Checking outage schedule post from news: {post_link}")
            post_html = self.fetch(post_link)
            if not post_html:
                continue
            post_blocks = self.extract_blocks(post_html)
            if post_blocks:
                collected_blocks.extend(post_blocks)

        return collected_blocks

    def fetch_latest_from_news(self) -> Optional[str]:
        """
        Fallback method: fetch the latest outage schedule article from news page.

        Iterates through top candidate links until a valid schedule page is fetched.

        Returns:
            HTML of the latest outage post or None.
        """
        news_html = self.fetch(self.NEWS_URL)
        if not news_html:
            return None

        links = self.find_news_schedule_links(news_html, limit=3)
        for post_link in links:
            logger.info(f"Fetching fallback outage schedule from news: {post_link}")
            post_html = self.fetch(post_link)
            if post_html and self.extract_blocks(post_html):
                return post_html

        return None


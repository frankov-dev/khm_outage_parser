"""
Web scraper for hoe.com.ua power outage schedules.

Extracts schedule blocks with dates from the official Khmelnytskoblenergo website.
Supports both modern text-based schedules (Ukrainian month names) and legacy image-based markers.
Includes retry mechanisms and news-section fallback.
"""

import logging
import re
from datetime import datetime
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
        - Multi-day blocks separated by <hr> or headings
        - Fallback fetching from company news if main page has no active schedule
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

    def fetch(self, url: Optional[str] = None) -> Optional[str]:
        """
        Fetch HTML content from website with retry mechanism.

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

        logger.error(f"All {self.MAX_RETRIES} attempts failed for {target_url}")
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

        # 1. Image alt match
        alt_match = self.ALT_DATE_PATTERN.search(text)
        if alt_match:
            d, m, y = alt_match.groups()
            y = f"20{y}" if len(y) == 2 else y
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"

        # 2. Ukrainian month match
        month_match = self.MONTHS_PATTERN.search(text)
        if month_match:
            d, m_name, y = month_match.groups()
            month = UKRAINIAN_MONTHS[m_name.lower()]
            year = int(y) if y else datetime.now(KYIV_TZ).year
            return f"{year:04d}-{month:02d}-{int(d):02d}"

        # 3. Numeric dot match
        num_match = self.NUMERIC_DATE_PATTERN.search(text)
        if num_match:
            d, m, y = num_match.groups()
            y = f"20{y}" if len(y) == 2 else y
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"

        return None

    def extract_blocks(self, html: str) -> list[dict[str, Any]]:
        """
        Extract schedule blocks from HTML.

        Each block contains a date (YYYY-MM-DD), schedule_text, and extras_text.

        Args:
            html: Raw HTML content

        Returns:
            List of dicts with keys: date, schedule_text, extras_text
        """
        soup = BeautifulSoup(html, "html.parser")
        content = (
            soup.find("div", class_="post")
            or soup.find("article")
            or soup.find("main")
            or soup.body
        )

        if not content:
            return []

        # Split content into sections by <hr> separators
        raw_children = list(content.children)
        sections: list[list[Any]] = [[]]
        for child in raw_children:
            if isinstance(child, Tag):
                if child.name == "hr":
                    sections.append([])
                else:
                    sections[-1].append(child)
            elif isinstance(child, str) and child.strip():
                sections[-1].append(child)

        blocks: list[dict[str, Any]] = []
        seen_dates: set[str] = set()

        for sec in sections:
            if not sec:
                continue

            sec_text = "\n".join(
                e.get_text("\n", strip=True) if isinstance(e, Tag) else str(e).strip()
                for e in sec
            )

            # Skip sections that do not contain outage schedules
            if "підчерг" not in sec_text.lower():
                continue

            # Check if this section contains date-marked images
            date_str = None
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

            # If no image date, detect date from section text
            if not date_str:
                date_str = self.extract_date(sec_text)

            # Fallback to Kyiv today's date if no date found
            if not date_str:
                date_str = datetime.now(KYIV_TZ).strftime("%Y-%m-%d")

            schedule_lines: list[str] = []
            extras_lines: list[str] = []
            keywords = [
                "підчерг", "відключення", "знеструм", "раніше", "довше",
                "додатково", "укренерго", "розпорядження", "триватиме", "заживлення", "скасовано"
            ]

            for raw_line in sec_text.split("\n"):
                line = raw_line.strip()
                if not line:
                    continue

                if re.search(r'підчерг[аи]?\s+\d\.\d', line, re.IGNORECASE):
                    # Distinguish base schedule line from operational change note
                    if any(kw in line.lower() for kw in ["раніше", "довше", "триватиме", "скасовано", "заживлення", "додатково"]):
                        extras_lines.append(line)
                    else:
                        schedule_lines.append(line)
                elif any(kw in line.lower() for kw in keywords):
                    if not line.startswith("Електроенергія у підчерг") and "перевірити чергу" not in line.lower():
                        extras_lines.append(line)

            if schedule_lines:
                blocks.append({
                    "date": date_str,
                    "schedule_text": "\n".join(schedule_lines),
                    "extras_text": "\n".join(extras_lines).strip()
                })
                seen_dates.add(date_str)

        return blocks

    def fetch_latest_from_news(self) -> Optional[str]:
        """
        Fallback method: fetch the latest outage schedule article from news page.

        Returns:
            HTML of the latest outage post or None.
        """
        news_html = self.fetch(self.NEWS_URL)
        if not news_html:
            return None

        soup = BeautifulSoup(news_html, "html.parser")
        post_link = None
        for a in soup.find_all("a"):
            title = a.get_text(strip=True).lower()
            href = a.get("href", "")
            if "post/" in href and any(w in title for w in ["погодинні відключення", "графік", "гпв"]):
                post_link = href
                break

        if not post_link:
            return None

        if not post_link.startswith("http"):
            post_link = f"https://hoe.com.ua{post_link}"

        logger.info(f"Fetching fallback outage schedule from news: {post_link}")
        return self.fetch(post_link)

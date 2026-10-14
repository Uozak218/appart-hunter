"""Century 21 scraper (corrected domain).

Real listing path: ``/annonces/location-appartement/v-paris/`` (was 410 on the
old ``/annonces/location/ville-paris/``). Detail URLs live under
``/trouver_logement/detail/<id>/``.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

CENTURY21_LISTING_URL = "https://www.century21.fr/annonces/location-appartement/v-paris/"
CENTURY21_BASE = "https://www.century21.fr"
_DETAIL_RE = re.compile(r"/trouver_logement/detail/(\d+)/?")


class Century21Scraper(BaseScraper):
    source = "century21"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        logger.info(f"[century21] fetching listing {CENTURY21_LISTING_URL}")
        try:
            await page.goto(
                CENTURY21_LISTING_URL,
                timeout=self.listing_timeout_ms,
                wait_until="domcontentloaded",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[century21] nav failed: {exc}")
            return []
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in soup.select('a[href*="/trouver_logement/detail/"]'):
            href = a.get("href") or ""
            m = _DETAIL_RE.search(href)
            if not m:
                continue
            ext = m.group(1)
            if ext in seen:
                continue
            seen.add(ext)
            if href.startswith("/"):
                href = CENTURY21_BASE + href
            entries.append({"external_id": ext, "url": href})
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[century21] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        return parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=CENTURY21_BASE
        )

"""Engel & Völkers Paris scraper — premium (engelvoelkers.com).

Segment luxe IDF. URL + selector validés 2026-05-15. Server-rendered.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

EV_LISTING_URL = (
    "https://www.engelvoelkers.com/fr/fr/propriete/res/louer/appartement/ile-de-france/paris"
)
EV_BASE = "https://www.engelvoelkers.com"
# /fr/fr/exposes/{uuid}
_DETAIL_RE = re.compile(r"/fr/fr/exposes/([a-f0-9-]{36})")


class EngelVoelkersScraper(BaseScraper):
    source = "engelvoelkers"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        logger.info(f"[engelvoelkers] fetching listing {EV_LISTING_URL}")
        try:
            await page.goto(
                EV_LISTING_URL, timeout=self.listing_timeout_ms, wait_until="domcontentloaded"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[engelvoelkers] nav failed: {exc}")
            return []
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in soup.select('a[href^="/fr/fr/exposes/"]'):
            href = a.get("href") or ""
            m = _DETAIL_RE.search(href)
            if not m:
                continue
            ext = m.group(1)
            if ext in seen:
                continue
            seen.add(ext)
            if href.startswith("/"):
                href = EV_BASE + href
            entries.append({"external_id": ext, "url": href})
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[engelvoelkers] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        return parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=EV_BASE
        )

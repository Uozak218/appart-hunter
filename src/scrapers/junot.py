"""Junot scraper — boutique parisienne (junot.fr).

URL + selector validated 2026-05-15. Server-rendered, ~30 listings/page.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

JUNOT_LISTING_URL = "https://www.junot.fr/fr/biens-immobiliers/louer/ile-de-france/paris"
JUNOT_BASE = "https://www.junot.fr"
# /fr/biens/{8-digit-id}-<slug>
_DETAIL_RE = re.compile(r"/fr/biens/(\d{8})-")


class JunotScraper(BaseScraper):
    source = "junot"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        logger.info(f"[junot] fetching listing {JUNOT_LISTING_URL}")
        try:
            await page.goto(
                JUNOT_LISTING_URL, timeout=self.listing_timeout_ms, wait_until="domcontentloaded"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[junot] nav failed: {exc}")
            return []
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in soup.select('a[href^="/fr/biens/"]'):
            href = a.get("href") or ""
            m = _DETAIL_RE.search(href)
            if not m:
                continue
            ext = m.group(1)
            if ext in seen:
                continue
            seen.add(ext)
            if href.startswith("/"):
                href = JUNOT_BASE + href
            entries.append({"external_id": ext, "url": href})
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[junot] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        return parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=JUNOT_BASE
        )

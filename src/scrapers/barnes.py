"""Barnes Paris scraper — premium boutique (barnes-paris.com).

Segment luxe — peu de location dans la fourchette < 2000€ mais zéro
concurrence quand il y a un match. URL + selector validés 2026-05-15.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

BARNES_LISTING_URL = "https://www.barnes-paris.com/fr/location/"
BARNES_BASE = "https://www.barnes-paris.com"
# /fr/location/paris-750XX/NNNNNNN
_DETAIL_RE = re.compile(r"/fr/location/paris-(75\d{3})/(\d{6,})")


class BarnesScraper(BaseScraper):
    source = "barnes"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        logger.info(f"[barnes] fetching listing {BARNES_LISTING_URL}")
        try:
            await page.goto(
                BARNES_LISTING_URL, timeout=self.listing_timeout_ms, wait_until="domcontentloaded"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[barnes] nav failed: {exc}")
            return []
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in soup.select('a[href*="/fr/location/paris-750"]'):
            href = a.get("href") or ""
            m = _DETAIL_RE.search(href)
            if not m:
                continue
            ext = m.group(2)
            if ext in seen:
                continue
            seen.add(ext)
            if href.startswith("/"):
                href = BARNES_BASE + href
            entries.append({"external_id": ext, "url": href, "postal_code": m.group(1)})
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[barnes] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        return parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=BARNES_BASE
        )

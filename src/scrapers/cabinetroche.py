"""Cabinet Roche scraper (corrected domain).

Real domain: ``cabinet-roche.fr`` (NOT ``.com`` which was a Lyon accounting
firm). Small Paris agency, mostly 75015 / 14e / 5e — ~8 rentals total.
Verbose detail URLs but plain HTML.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

CABINETROCHE_LISTING_URL = "https://www.cabinet-roche.fr/location/1"
CABINETROCHE_BASE = "https://www.cabinet-roche.fr"
# /location/75-paris/<n>-paris/<n>-paris-<n>eme-arrondissement/<n>-<slug>/t/<n>-<slug>/
_DETAIL_RE = re.compile(r"/location/75-paris/[^\"]+/t/(\d+)-[a-z0-9-]+/?")


class CabinetRocheScraper(BaseScraper):
    source = "cabinetroche"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        logger.info(f"[cabinetroche] fetching listing {CABINETROCHE_LISTING_URL}")
        try:
            await page.goto(
                CABINETROCHE_LISTING_URL,
                timeout=self.listing_timeout_ms,
                wait_until="domcontentloaded",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[cabinetroche] nav failed: {exc}")
            return []
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in soup.select('a[href*="/location/75-paris/"]'):
            href = a.get("href") or ""
            m = _DETAIL_RE.search(href)
            if not m:
                continue
            ext = m.group(1)
            if ext in seen:
                continue
            seen.add(ext)
            if href.startswith("/"):
                href = CABINETROCHE_BASE + href
            entries.append({"external_id": ext, "url": href})
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[cabinetroche] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        return parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=CABINETROCHE_BASE
        )

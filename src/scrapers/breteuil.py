"""Breteuil Immobilier scraper (corrected domain).

Real domain: ``breteuilhomes.com`` (not ``breteuil.com``). Next.js with SSR —
detail anchors are present in initial HTML.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

BRETEUIL_LISTING_URL = "https://breteuilhomes.com/louer/paris"
BRETEUIL_BASE = "https://breteuilhomes.com"
# /proprietes/<slug>-<id>
_DETAIL_RE = re.compile(r"/proprietes/([a-z0-9-]+-\d{4,})")


class BreteuilScraper(BaseScraper):
    source = "breteuil"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        logger.info(f"[breteuil] fetching listing {BRETEUIL_LISTING_URL}")
        try:
            await page.goto(
                BRETEUIL_LISTING_URL, timeout=self.listing_timeout_ms, wait_until="domcontentloaded"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[breteuil] nav failed: {exc}")
            return []
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in soup.select('a[href*="/proprietes/"]'):
            href = a.get("href") or ""
            m = _DETAIL_RE.search(href)
            if not m:
                continue
            ext = m.group(1)
            if ext in seen:
                continue
            seen.add(ext)
            if href.startswith("/"):
                href = BRETEUIL_BASE + href
            entries.append({"external_id": ext, "url": href})
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[breteuil] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        return parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=BRETEUIL_BASE
        )

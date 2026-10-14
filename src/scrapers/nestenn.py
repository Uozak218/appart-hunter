"""Nestenn scraper (corrected URL + post-fetch Paris filter).

Real listing URL: ``/location/appartement/paris-75``. Detail anchors are
**relative** slugs ending in ``-ref-<id>``; we absolutize them. The
``paris-75`` page leaks some 93/Bagnolet listings — we filter post-fetch
on the URL slug containing ``paris``.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

NESTENN_LISTING_URL = "https://nestenn.com/location/appartement/paris-75"
NESTENN_BASE = "https://nestenn.com"
_DETAIL_RE = re.compile(r"([a-z0-9-]+-ref-\d+)/?$", re.I)


class NestennScraper(BaseScraper):
    source = "nestenn"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        logger.info(f"[nestenn] fetching listing {NESTENN_LISTING_URL}")
        try:
            await page.goto(
                NESTENN_LISTING_URL, timeout=self.listing_timeout_ms, wait_until="domcontentloaded"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[nestenn] nav failed: {exc}")
            return []
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in soup.select('a[href*="-ref-"]'):
            href = a.get("href") or ""
            m = _DETAIL_RE.search(href)
            if not m:
                continue
            slug = m.group(1)
            # Post-fetch Paris filter (the listing page leaks Bagnolet/93 etc.).
            if "paris" not in slug.lower():
                continue
            ext = re.search(r"ref-(\d+)", slug).group(1)
            if ext in seen:
                continue
            seen.add(ext)
            abs_url = urljoin(NESTENN_LISTING_URL + "/", href)
            entries.append({"external_id": ext, "url": abs_url})
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[nestenn] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        return parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=NESTENN_BASE
        )

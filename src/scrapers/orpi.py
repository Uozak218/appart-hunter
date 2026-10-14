"""Orpi scraper.

Largest French agency network. Public Paris rentals page, server-rendered.
URL + selector validated against the live site on 2026-05-15.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

ORPI_LISTING_URL = "https://www.orpi.com/location-immobiliere-paris/louer-appartement/"
ORPI_BASE = "https://www.orpi.com"


class OrpiScraper(BaseScraper):
    source = "orpi"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        logger.info(f"[orpi] fetching listing {ORPI_LISTING_URL}")
        try:
            await page.goto(
                ORPI_LISTING_URL,
                timeout=self.listing_timeout_ms,
                wait_until="domcontentloaded",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[orpi] nav failed: {exc}")
            return []
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        # URL pattern: /annonce-location-appartement-tN-paris-XX-750XX-{uuid}/
        for a in soup.select('a[href^="/annonce-location-"]'):
            href = a.get("href") or ""
            m = re.search(r"-([a-f0-9]{8}-[a-f0-9-]{12,})/?$", href)
            if not m:
                continue
            ext = m.group(1)
            if ext in seen:
                continue
            seen.add(ext)
            # URL slug encodes postcode: /annonce-location-appartement-tN-paris-XX-750XX-uuid
            pc_m = re.search(r"-(75\d{3})-[a-f0-9]{8}", href)
            entry = {"external_id": ext, "url": ORPI_BASE + href}
            if pc_m:
                entry["postal_code"] = pc_m.group(1)
            entries.append(entry)
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[orpi] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        return parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=ORPI_BASE
        )

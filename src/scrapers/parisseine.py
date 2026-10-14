"""Paris Seine Immobilier scraper.

Rebrand: l'ancien paris-seine.com renvoie 500. Le vrai site est
``paris-seine-immobilier.com`` — encodé ISO-8859-1. Validé 2026-05-15.
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

PARISSEINE_LISTING_URL = "https://www.paris-seine-immobilier.com/catalog/"
PARISSEINE_BASE = "https://www.paris-seine-immobilier.com"
# /fiches/<num>-<num>-<num>_<8-digit-id>/
_DETAIL_RE = re.compile(r"/fiches/\d+-\d+-\d+_(\d{8})/")


class ParisSeineScraper(BaseScraper):
    source = "parisseine"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        logger.info(f"[parisseine] fetching listing {PARISSEINE_LISTING_URL}")
        try:
            await page.goto(
                PARISSEINE_LISTING_URL,
                timeout=self.listing_timeout_ms,
                wait_until="domcontentloaded",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[parisseine] nav failed: {exc}")
            return []
        # Site is ISO-8859-1; Playwright's page.content() returns decoded text,
        # so BeautifulSoup gets it right via Latin-1 fallback.
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in soup.select('a[href*="/fiches/"]'):
            href = a.get("href") or ""
            m = _DETAIL_RE.search(href)
            if not m:
                continue
            # /catalog/ surfaces both rent + sale — filter on URL family.
            # Rental URLs contain "Location" or "location" somewhere; sale = "Vente".
            if "vente" in href.lower():
                continue
            ext = m.group(1)
            if ext in seen:
                continue
            seen.add(ext)
            # Site uses relative hrefs like "../fiches/..." — urljoin resolves
            # them against the listing-page URL.
            abs_url = urljoin(PARISSEINE_LISTING_URL, href)
            entries.append({"external_id": ext, "url": abs_url})
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[parisseine] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        return parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=PARISSEINE_BASE
        )

"""District Immobilier scraper (corrected domain + HTTP-only).

Real domain: ``district-immo.com``. WordPress site with static HTML — curl
returns 200, but Playwright is fingerprinted and blocked with 403. So we
use httpx directly (use_http_only=True) — gratuit et fiable.

Stock principalement vente, location limitée.
"""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

DISTRICT_LISTING_URL = "https://www.district-immo.com/louer/"
DISTRICT_BASE = "https://www.district-immo.com"
_DETAIL_RE = re.compile(r"/biens/([a-z0-9-]{6,})/?")
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}


class DistrictScraper(BaseScraper):
    source = "district"
    use_http_only = True

    async def fetch_listing_page(self, page) -> list[dict[str, str]]:  # noqa: ARG002
        logger.info(f"[district] fetching {DISTRICT_LISTING_URL}")
        try:
            async with httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True) as c:
                resp = await c.get(DISTRICT_LISTING_URL)
                resp.raise_for_status()
                html = resp.text
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[district] fetch failed: {exc}")
            return []
        soup = BeautifulSoup(html, "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        # Detail anchors: <a href="https://www.district-immo.com/biens/<slug>/">
        for a in soup.select('a[href*="/biens/"]'):
            href = a.get("href") or ""
            # Skip social-share helper URLs that embed /biens/ in a query string.
            if "pinterest.com" in href or "facebook.com" in href or "plus.google.com" in href:
                continue
            m = _DETAIL_RE.search(href)
            if not m:
                continue
            ext = m.group(1)
            if ext in seen:
                continue
            # Only keep Paris listings (skip Châtillon, etc.).
            if "paris" not in ext.lower():
                continue
            seen.add(ext)
            if href.startswith("/"):
                href = DISTRICT_BASE + href
            entries.append({"external_id": ext, "url": href})
        return entries

    async def parse_detail(self, page, entry: dict[str, str]) -> RawListing | None:  # noqa: ARG002
        url = entry["url"]
        try:
            async with httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True) as c:
                resp = await c.get(url)
                resp.raise_for_status()
                html = resp.text
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[district] detail fetch failed {url}: {exc}")
            return None
        soup = BeautifulSoup(html, "lxml")
        return parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=DISTRICT_BASE
        )

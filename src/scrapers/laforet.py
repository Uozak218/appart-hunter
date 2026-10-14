"""Laforêt scraper.

JS-rendered list of Paris rentals on laforet.com. The detail anchors don't
appear directly in initial HTML, but ``<article data-counter-id-value="ID">``
cards do — each wraps the detail ``<a>``. We pull id + URL from there and
filter on postcode (75xxx) to drop any non-Paris contamination.

Validated 2026-05-15: 40 cards on page, all Paris.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

LAFORET_LISTING_URL = "https://www.laforet.com/departement/location-appartement-paris"
LAFORET_BASE = "https://www.laforet.com"


class LaforetScraper(BaseScraper):
    source = "laforet"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        logger.info(f"[laforet] fetching listing {LAFORET_LISTING_URL}")
        try:
            resp = await page.goto(
                LAFORET_LISTING_URL,
                timeout=self.listing_timeout_ms,
                wait_until="domcontentloaded",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[laforet] nav failed: {exc}")
            return []
        # A non-200 here is an anti-bot challenge (seen: HTTP 405 + captcha page),
        # not a genuinely empty listing. Flag it loudly so "0 entries" doesn't read
        # as "no ads" — and bail early since the challenge page has no cards.
        if resp is not None and resp.status != 200:
            logger.warning(
                f"[laforet] blocked: HTTP {resp.status} (likely anti-bot challenge) "
                f"— returning 0 entries"
            )
            return []
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for art in soup.select("article[data-counter-id-value]"):
            cid = art.get("data-counter-id-value")
            if not cid or cid in seen:
                continue
            link = art.select_one("a[href*='/louer/']")
            if not link:
                continue
            href = link.get("href") or ""
            # Drop non-Paris listings (e.g. SAINT PIERRE DU MONT) — keep only 75xxx.
            text = art.get_text(" ", strip=True)
            pc_match = re.search(r"\b(75\d{3})\b", text)
            if not pc_match:
                continue
            seen.add(cid)
            if href.startswith("/"):
                href = LAFORET_BASE + href
            entries.append(
                {"external_id": cid, "url": href, "postal_code": pc_match.group(1)}
            )
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[laforet] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        return parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=LAFORET_BASE
        )

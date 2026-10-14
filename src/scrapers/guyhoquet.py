"""Guy Hoquet scraper — skeleton.

Public Paris rentals page on guy-hoquet.com. HTML parsing only.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.schemas.listing import RawListing
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

GUY_HOQUET_LISTING_URL = (
    "https://www.guy-hoquet.com/biens-immobiliers/?transaction=location&type=appartement&city=paris"
)


class GuyHoquetScraper(BaseScraper):
    source = "guyhoquet"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        logger.info(f"[guyhoquet] fetching listing {GUY_HOQUET_LISTING_URL}")
        try:
            await page.goto(
                GUY_HOQUET_LISTING_URL,
                timeout=self.listing_timeout_ms,
                wait_until="domcontentloaded",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[guyhoquet] nav failed: {exc}")
            return []
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in soup.select("a[href*='/annonce/'], a[href*='/bien/']"):
            href = a.get("href") or ""
            m = re.search(r"/(?:annonce|bien)/([a-z0-9-]{6,})/?", href, re.I)
            if not m:
                continue
            ext = m.group(1)
            if ext in seen:
                continue
            seen.add(ext)
            if href.startswith("/"):
                href = "https://www.guy-hoquet.com" + href
            entries.append({"external_id": ext, "url": href})
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[guyhoquet] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        title = _text(soup.select_one("h1"))
        description = _text(
            soup.select_one(".description, [itemprop='description'], .annonce-description")
        )
        price_text = _text(soup.select_one("[class*='rice'], [class*='rix'], .price"))
        body_text = soup.get_text(" ", strip=True)

        rent = _parse_price(price_text) or _parse_price(body_text)
        surface = _parse_surface(body_text)
        rooms_src = title + " " + body_text
        rooms_match = re.search(r"\bT\s?(\d)\b", rooms_src, re.I) or re.search(
            r"(\d)\s*pi[èe]ces?", rooms_src, re.I
        )
        rooms = int(rooms_match.group(1)) if rooms_match else None
        if rooms is None and re.search(r"\bstudio\b", title, re.I):
            rooms = 1
        postcode_match = re.search(r"\b(75\d{3})\b", body_text)
        photos = [
            img.get("data-src") or img.get("src")
            for img in soup.select(".carousel img, .gallery img, picture img, [class*='hoto'] img")[
                :15
            ]
            if img.get("data-src") or img.get("src")
        ]
        return RawListing(
            source=self.source,
            external_id=entry["external_id"],
            url=url,
            title=title,
            description=description,
            rent_cc=rent,
            surface_m2=surface,
            rooms=rooms,
            property_type=f"T{rooms}" if rooms else None,
            postal_code=postcode_match.group(1) if postcode_match else None,
            photo_urls=[p for p in photos if p and p.startswith("http")],
        )


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _parse_price(text: str) -> float | None:
    m = re.search(r"([\d\s .,]+)\s*€", text or "")
    if not m:
        return None
    cleaned = re.sub(r"[^\d,.]", "", m.group(1)).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_surface(text: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m", text or "")
    if not m:
        return None
    return float(m.group(1).replace(",", "."))

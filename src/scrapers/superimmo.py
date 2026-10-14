"""Superimmo scraper — skeleton.

Superimmo.com aggregates rental listings from multiple agencies. The structure
below mirrors the other HTML-based scrapers (Logic-Immo, PAP): listing URL +
card selectors to validate on first run.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.schemas.listing import RawListing
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

SUPERIMMO_LISTING_URL = (
    "https://www.superimmo.com/location/appartement/ile-de-france/paris"
    "?prix-max={max_price}&surface-min={min_surface}"
)


class SuperImmoScraper(BaseScraper):
    source = "superimmo"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        c = self.criteria
        min_surface = int(min(c.property.min_surface.values())) if c.property.min_surface else 40
        url = SUPERIMMO_LISTING_URL.format(
            max_price=int(c.property.max_rent_cc),
            min_surface=min_surface,
        )
        logger.info(f"[superimmo] fetching listing {url}")
        try:
            await page.goto(url, timeout=self.listing_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[superimmo] nav failed: {exc}")
            return []
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in soup.select("a[href*='/annonces/location-']"):
            href = a.get("href")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.superimmo.com" + href
            # URLs look like /annonces/location-appartement-61m-paris-12eme-75012-x10xjp2
            m = re.search(r"-([a-z0-9]{6,})/?$", href)
            if not m:
                continue
            ext = m.group(1)
            if ext in seen:
                continue
            seen.add(ext)
            entries.append({"external_id": ext, "url": href})
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[superimmo] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        title = (soup.select_one("h1") or soup.new_tag("h1")).get_text(strip=True)
        description = _text(
            soup.select_one(".description, [itemprop='description'], .annonce-description")
        )
        price_text = _text(soup.select_one(".price, [itemprop='price'], .annonce-price"))
        body_text = soup.get_text(" ", strip=True)

        rent = _parse_price(price_text) or _parse_price(body_text)
        surface = _parse_surface(body_text)
        rooms_src = title + " " + body_text
        rooms_match = re.search(r"\bT\s?(\d)\b", rooms_src, re.I) or re.search(
            r"(\d)\s*pi[èe]ces?", rooms_src, re.I
        )
        rooms = int(rooms_match.group(1)) if rooms_match else None
        postcode_match = re.search(r"\b(75\d{3})\b", body_text)
        photos = [
            img.get("data-src") or img.get("src")
            for img in soup.select(".carousel img, .gallery img, .photos img")[:15]
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
            photo_urls=[p for p in photos if p],
        )


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _parse_price(text: str) -> float | None:
    m = re.search(r"([\d\s\u00a0.,]+)\s*€", text or "")
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

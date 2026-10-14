"""PAP scraper — skeleton.

PAP historically has relatively lenient anti-bot. Full implementation TODO:
parse `.search-results-list-item` cards on the listing page and the detail
page's structured-data JSON.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.schemas.listing import RawListing
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

PAP_LISTING_URL = (
    "https://www.pap.fr/annonce/locations-appartement-paris-75-g439"
    "-jusqu-a-{max_price}-euros-a-partir-de-{min_surface}-m2"
)


class PapScraper(BaseScraper):
    source = "pap"
    # Cloudflare Turnstile — Scrapfly's ASP solves the JS challenge.
    # PAP renders server-side once unblocked, so render_js stays off (saves ~5× credits).
    use_scrapfly = True
    scrapfly_render_js = False

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        c = self.criteria
        min_surface = int(min(c.property.min_surface.values())) if c.property.min_surface else 40
        url = PAP_LISTING_URL.format(max_price=int(c.property.max_rent_cc), min_surface=min_surface)
        logger.info(f"[pap] fetching listing {url}")
        await page.goto(url, timeout=self.listing_timeout_ms, wait_until="domcontentloaded")
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in soup.select("a.item-title, a[href*='/annonces/']"):
            href = a.get("href")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.pap.fr" + href
            m = re.search(r"/([a-z0-9-]+-r\d+)/?", href)
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
            logger.warning(f"[pap] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        title = (soup.select_one("h1") or soup.new_tag("h1")).get_text(strip=True)
        description = _text(soup.select_one(".item-description, .annonce-description"))
        price_text = _text(soup.select_one(".item-price, .price"))
        surface_text = _text(soup.select_one(".item-summary"))

        rent = _parse_price(price_text)
        surface = _parse_surface(surface_text)
        rooms = _rooms(title)
        postcode_match = re.search(r"\b(75\d{3})\b", surface_text + " " + title)
        photos = [
            img.get("data-src") or img.get("src")
            for img in soup.select(".carousel img, .owl-item img")[:15]
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


def _rooms(title: str) -> int | None:
    m = re.search(r"\bT(\d)\b", title or "", re.I)
    return int(m.group(1)) if m else None

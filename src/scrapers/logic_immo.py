"""Logic-Immo scraper — skeleton.

Logic-Immo shares infrastructure with SeLoger. The structure below is the
starting point: listing URL + card selectors to validate on first run.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.schemas.listing import RawListing
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

LOGIC_IMMO_URL = (
    "https://www.logic-immo.com/louer-immobilier/location-paris,75-{arrondissements}"
    "-{max_price}eur-{min_surface}m2"
)


class LogicImmoScraper(BaseScraper):
    source = "logic_immo"
    # DataDome (same vendor as SeLoger). Server-rendered → no JS render needed.
    use_scrapfly = True
    scrapfly_render_js = False

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        c = self.criteria
        arr = ",".join(str(int(z.code[2:])) for z in c.zones.include if z.code.startswith("75"))
        url = LOGIC_IMMO_URL.format(
            arrondissements=arr or "01,02,03,04,05,09,10,11",
            max_price=int(c.property.max_rent_cc),
            min_surface=int(min(c.property.min_surface.values())) if c.property.min_surface else 40,
        )
        logger.info(f"[logic_immo] fetching listing {url}")
        try:
            await page.goto(url, timeout=self.listing_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[logic_immo] nav failed: {exc}")
            return []
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        for card in soup.select("[data-id], a[href*='/detail-location/']"):
            href = (
                card.get("href")
                if card.name == "a"
                else (card.select_one("a[href*='/detail-location/']") or {})
            )
            if isinstance(href, dict):
                continue
            data_id = card.get("data-id") or (re.search(r"(\d{6,})", href or "") if href else None)
            if not href or not data_id:
                continue
            if href.startswith("/"):
                href = "https://www.logic-immo.com" + href
            ext = data_id.group(1) if hasattr(data_id, "group") else str(data_id)
            entries.append({"external_id": ext, "url": href})
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[logic_immo] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        title = (soup.select_one("h1") or soup.new_tag("h1")).get_text(strip=True)
        description = (
            soup.select_one(".description, [itemprop='description']") or soup.new_tag("p")
        ).get_text(" ", strip=True)
        price_text = (
            soup.select_one(".offer-price, [itemprop='price']") or soup.new_tag("p")
        ).get_text(" ", strip=True)
        m_price = re.search(r"([\d\s\u00a0.,]+)\s*€", price_text)
        rent = float(re.sub(r"[^\d.]", "", m_price.group(1).replace(",", "."))) if m_price else None
        m_surf = re.search(r"(\d+(?:[.,]\d+)?)\s*m", soup.get_text(" "))
        surface = float(m_surf.group(1).replace(",", ".")) if m_surf else None
        postcode_match = re.search(r"\b(75\d{3})\b", soup.get_text(" "))
        rooms_match = re.search(r"\bT(\d)\b", title, re.I)
        rooms = int(rooms_match.group(1)) if rooms_match else None
        photos = [
            img.get("data-src") or img.get("src")
            for img in soup.select(".carousel img, .gallery img")[:15]
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

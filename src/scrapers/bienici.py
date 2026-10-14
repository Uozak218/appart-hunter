"""Bien'ici scraper — skeleton.

Bien'ici exposes a public JSON realEstateAds endpoint that's much friendlier
than scraping rendered HTML. Baseline URL + params below — tune the
``realEstateTypes`` and ``filterType`` list when you run it live.
"""

from __future__ import annotations

import json

import httpx
from playwright.async_api import Page

from src.schemas.listing import RawListing
from src.scrapers.base import BaseScraper
from src.scrapers.proxies import get_httpx_proxy
from src.utils.logging import logger

BIENICI_API = "https://www.bienici.com/realEstateAds.json"
# Bien'ici encodes zones as negative integers internal to their system.
# Paris intra-muros (all 20 arrondissements) = -71525 (verified 2026-04).
PARIS_ZONE_ID = "-71525"


class BienIciScraper(BaseScraper):
    source = "bienici"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        # We don't need Playwright for Bien'ici — a simple httpx call is enough.
        c = self.criteria
        params = {
            "filters": json.dumps(
                {
                    "size": 50,
                    "from": 0,
                    "filterType": "rent",
                    "propertyType": ["flat"],
                    "page": 1,
                    "sortBy": "relevance",
                    "sortOrder": "desc",
                    "onTheMarket": [True],
                    "maxPrice": int(c.property.max_rent_cc),
                    "minArea": int(min(c.property.min_surface.values()))
                    if c.property.min_surface
                    else 40,
                    "zoneIdsByTypes": {"zoneIds": [PARIS_ZONE_ID]},
                }
            ),
            "extensionType": "extendedIfNoResult",
        }
        try:
            async with httpx.AsyncClient(
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "fr-FR,fr;q=0.9",
                    "Referer": "https://www.bienici.com/recherche/location/paris-75",
                },
                proxy=get_httpx_proxy(),
                timeout=20.0,
            ) as client:
                r = await client.get(BIENICI_API, params=params)
                r.raise_for_status()
                data = r.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[bienici] API call failed: {exc}")
            return []

        entries: list[dict[str, str]] = []
        for ad in data.get("realEstateAds", []):
            aid = ad.get("id")
            if not aid:
                continue
            url = ad.get("url") or f"https://www.bienici.com/annonce/location/{aid}"
            if url.startswith("/"):
                url = "https://www.bienici.com" + url
            entries.append({"external_id": str(aid), "url": url, "ad": ad})
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        ad = entry.get("ad") or {}
        photos = [p.get("url") for p in (ad.get("photos") or []) if p.get("url")]
        return RawListing(
            source=self.source,
            external_id=str(ad.get("id") or entry["external_id"]),
            url=str(entry["url"]),
            title=ad.get("title") or ad.get("adTitle") or "",
            description=ad.get("description"),
            rent_cc=ad.get("price"),
            charges=ad.get("charges"),
            surface_m2=ad.get("surfaceArea"),
            rooms=ad.get("roomsQuantity"),
            property_type=(f"T{ad.get('roomsQuantity')}" if ad.get("roomsQuantity") else None),
            furnished=ad.get("isFurnished"),
            floor=ad.get("floor"),
            has_elevator=ad.get("hasElevator"),
            postal_code=ad.get("postalCode"),
            latitude=(ad.get("blurInfo") or {}).get("position", {}).get("lat"),
            longitude=(ad.get("blurInfo") or {}).get("position", {}).get("lon"),
            address=ad.get("city"),
            photo_urls=photos,
            raw=ad,
        )

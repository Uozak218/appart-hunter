"""Locservice.fr scraper.

100% particulier-à-particulier — *le* gisement le plus prometteur pour
trouver une pépite sans frais d'agence + faible concurrence d'autres
candidats issus des grands agrégateurs.

Static HTML (HTTP-only), zéro anti-bot. Encodage utf-8 (le header
``Content-Type`` est correct en 2026).
"""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import Page  # noqa: F401 — kept for signature compatibility

from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

LOCSERVICE_LISTING_URL = "https://www.locservice.fr/paris-75/location-particulier.html"
LOCSERVICE_BASE = "https://www.locservice.fr"
# Detail URL examples:
#   /paris-75/location-appartement-paris-11/123456
#   /paris-75/location-studio-paris-15/987654
_DETAIL_RE = re.compile(
    r"/paris-75/location-(?:appartement|maison|studio|chambre)-paris-(\d{2})/(\d+)",
    re.I,
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def _decode(resp: httpx.Response) -> str:
    """Locservice servait latin-1 il y a quelques mois mais sert utf-8
    en 2026 (vérifié par fingerprint des caractères é/è dans le corps).
    On laisse httpx décoder (respecte le header Content-Type), avec fallback
    utf-8 si pas d'header explicite."""
    return resp.text


class LocserviceScraper(BaseScraper):
    source = "locservice"
    use_http_only = True

    async def fetch_listing_page(self, page) -> list[dict[str, str]]:  # noqa: ARG002
        logger.info(f"[locservice] fetching {LOCSERVICE_LISTING_URL}")
        try:
            async with httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True) as c:
                resp = await c.get(LOCSERVICE_LISTING_URL)
                resp.raise_for_status()
                html = _decode(resp)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[locservice] fetch failed: {exc}")
            return []
        soup = BeautifulSoup(html, "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in soup.select('a[href*="/paris-75/location-"]'):
            href = a.get("href") or ""
            m = _DETAIL_RE.search(href)
            if not m:
                continue
            arr2, ad_id = m.group(1), m.group(2)
            if ad_id in seen:
                continue
            seen.add(ad_id)
            if href.startswith("/"):
                href = LOCSERVICE_BASE + href
            # Zero-pad to 3 digits so arr 6 → "75006" (not "7506") matches the
            # criteria zone format.
            postcode = f"75{int(arr2):03d}"
            entries.append(
                {
                    "external_id": ad_id,
                    "url": href,
                    "postal_code": postcode,
                }
            )
        return entries

    async def parse_detail(self, page, entry: dict[str, str]) -> RawListing | None:  # noqa: ARG002
        url = entry["url"]
        try:
            async with httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True) as c:
                resp = await c.get(url)
                resp.raise_for_status()
                html = _decode(resp)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[locservice] detail fetch failed {url}: {exc}")
            return None
        soup = BeautifulSoup(html, "lxml")
        raw = parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=LOCSERVICE_BASE
        )
        # Locservice annonces are private-individual posts → tag poster_type
        # so scoring/templates know it's a particulier (no agency fee, faster
        # to contact). The Listing model picks this up downstream.
        raw.poster_type = "particulier"
        return raw

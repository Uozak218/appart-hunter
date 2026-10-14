"""Espaces Atypiques scraper.

Boutique parisienne spécialisée biens caractère (lofts, ateliers, duplex,
appartements haussmanniens rénovés). Pile dans la cible "pépite cachet".

Exploite l'**API REST WordPress publique** ``/wp-json/wp/v2/locations`` —
pas besoin de Playwright ni de Scrapfly (HTTP-only, gratuit, instantané).
Filtre côté client sur slug ``75xxx`` (Paris) et exclut les meublés.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from src.schemas.listing import RawListing
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

API_URL = (
    "https://www.espaces-atypiques.com/wp-json/wp/v2/locations"
    "?per_page=100&orderby=date&order=desc&_embed=true"
)
_PARIS_SLUG_RE = re.compile(r"^75\d{3}")
_FURNISHED_HINT_RE = re.compile(r"\bmeubl[ée]?", re.I)


class EspacesAtypiquesScraper(BaseScraper):
    source = "espaces_atypiques"
    use_http_only = True

    async def fetch_listing_page(self, page) -> list[dict[str, Any]]:  # noqa: ARG002
        logger.info(f"[{self.source}] fetching WP REST {API_URL}")
        async with httpx.AsyncClient(
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
        ) as client:
            resp = await client.get(API_URL)
            resp.raise_for_status()
            items: list[dict[str, Any]] = resp.json()

        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for it in items:
            slug = it.get("slug") or ""
            if not _PARIS_SLUG_RE.match(slug):
                continue
            # Exclude furnished — user wants non-meublé pépites.
            if _FURNISHED_HINT_RE.search(slug):
                continue
            ext = str(it.get("id") or "")
            if not ext or ext in seen:
                continue
            seen.add(ext)
            entries.append({"external_id": ext, "url": it.get("link") or "", "raw": it})
        logger.debug(
            f"[{self.source}] WP REST returned {len(items)} items, {len(entries)} Paris non-meublé"
        )
        return entries

    async def parse_detail(self, page, entry: dict[str, Any]) -> RawListing | None:  # noqa: ARG002
        raw: dict[str, Any] = entry.get("raw") or {}
        acf: dict[str, Any] = raw.get("acf") or {}

        title = _strip_html((raw.get("title") or {}).get("rendered") or "")
        content_html = (raw.get("content") or {}).get("rendered") or ""
        description = _strip_html(content_html) or None

        loyer = _to_float(acf.get("location_loyer"))
        charges = _to_float(acf.get("location_provision_charges"))
        type_loyer = (acf.get("location_type_loyer") or "").upper()
        # HC = hors charges; we report rent_cc including charges.
        rent_cc = loyer + charges if loyer is not None and type_loyer == "HC" and charges else loyer

        surface = _to_float(acf.get("bien_surface_habitable"))
        rooms = _to_int(acf.get("bien_nombre_pieces"))
        floor = _to_int(acf.get("bien_etage"))

        geo = acf.get("bien_geolocalisation") or {}
        lat = _to_float(geo.get("lat"))
        lon = _to_float(geo.get("lng"))
        address = geo.get("address") or None
        postcode = acf.get("bien_code_postal")
        if not postcode and address:
            m = re.search(r"\b(75\d{3})\b", address)
            if m:
                postcode = m.group(1)

        photos = _gather_photos(raw, content_html)

        return RawListing(
            source=self.source,
            external_id=entry["external_id"],
            url=entry["url"],
            title=title,
            description=description,
            rent_cc=rent_cc,
            charges=charges,
            surface_m2=surface,
            rooms=rooms,
            property_type=f"T{rooms}" if rooms else None,
            furnished=False,
            floor=floor,
            postal_code=str(postcode) if postcode else None,
            latitude=lat,
            longitude=lon,
            address=address,
            photo_urls=photos,
            raw={"acf": acf},
        )


def _strip_html(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


def _to_float(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ".").split()[0])
    except (ValueError, TypeError):
        return None


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


def _gather_photos(raw: dict[str, Any], content_html: str) -> list[str]:
    photos: list[str] = []
    seen: set[str] = set()

    def _add(src: str | None) -> None:
        if src and src.startswith("http") and src not in seen:
            seen.add(src)
            photos.append(src)

    emb = raw.get("_embedded") or {}
    for m in emb.get("wp:featuredmedia") or []:
        _add(m.get("source_url"))
        for size in (m.get("media_details") or {}).get("sizes", {}).values():
            _add(size.get("source_url"))

    if content_html:
        soup = BeautifulSoup(content_html, "lxml")
        for img in soup.select("img")[:25]:
            _add(img.get("data-src") or img.get("src"))

    return photos[:15]

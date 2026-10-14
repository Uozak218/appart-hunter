"""GLF (Guy Loïck Fouchet) scraper — Netty.immo-powered Paris agency.

Small agency site (~50 rentals). Server-rendered HTML, no anti-bot.
Listings at ``/services/devenir-locataire?page=N``, details at ``/flats/{id}``.
Photos on ``img.netty.immo``.

The site has no location filter: we walk the whole rental stock and tag each
entry with the postcode shown on its card ("92100 - Boulogne-Billancourt"),
so the base zone pre-filter drops out-of-zone flats before any detail fetch.
2026-10-14: stock is Paris + Boulogne only — nothing in the 92-nord / 93 towns.
"""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from src.schemas.listing import RawListing
from src.scrapers._common import parse_price, parse_surface, text_of
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

GLF_BASE = "https://www.glf.eu"
GLF_LISTING_URL = f"{GLF_BASE}/services/devenir-locataire"

_FLAT_RE = re.compile(r"/flats/(\d+)")
# Any French postcode (01xxx–98xxx); the look-behind skips refs like "A-05073".
_POSTCODE_RE = re.compile(r"(?<![\d-])((?:0[1-9]|[1-8]\d|9[0-8])\d{3})(?!\d)")
_CARD_POSTCODE_RE = re.compile(r"\b(\d{5})\s*-\s*[A-Za-zÀ-ÿ]")


class GlfScraper(BaseScraper):
    source = "glf"
    use_http_only = True

    async def fetch_listing_page(self, page) -> list[dict[str, str]]:  # noqa: ARG002
        entries: list[dict[str, str]] = []
        seen: set[str] = set()

        for page_num in range(1, 6):
            url = GLF_LISTING_URL if page_num == 1 else f"{GLF_LISTING_URL}?page={page_num}"
            logger.info(f"[glf] fetching page {page_num}")
            try:
                html = await _fetch(url)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[glf] page {page_num} failed: {exc}")
                break

            soup = BeautifulSoup(html, "lxml")
            page_count = 0

            for a in soup.select("a[href*='/flats/']"):
                href = a.get("href", "")
                m = _FLAT_RE.search(href)
                if not m:
                    continue
                ext_id = m.group(1)
                if ext_id in seen:
                    continue
                seen.add(ext_id)
                full_url = href if href.startswith("http") else f"{GLF_BASE}{href}"
                entry = {"external_id": ext_id, "url": full_url}
                pc = card_postcode(a)
                if pc:
                    entry["postal_code"] = pc
                entries.append(entry)
                page_count += 1

            if page_count == 0:
                break
            if not soup.select(f"a[href*='page={page_num + 1}']"):
                break

        return entries

    async def parse_detail(self, page, entry: dict[str, str]) -> RawListing | None:  # noqa: ARG002
        url = entry["url"]
        try:
            html = await _fetch(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[glf] detail fetch failed {url}: {exc}")
            return None

        soup = BeautifulSoup(html, "lxml")
        body = soup.get_text(" ", strip=True)
        title = text_of(soup.select_one("h1")) or text_of(soup.select_one("h3"))

        rent_m = re.search(r"([\d\s .,]+)\s*€\s*/\s*mois", body)
        rent = parse_price(rent_m.group(0)) if rent_m else parse_price(body)

        surface = parse_surface(body)
        rooms_m = re.search(r"(\d+)\s*pièce", body, re.I) or re.search(r"\bT(\d)\b", title, re.I)
        rooms = int(rooms_m.group(1)) if rooms_m else None
        if rooms is None and re.search(r"\bstudio\b", title, re.I):
            rooms = 1

        # Card postcode first; the body also carries the agency address (75003).
        postcode_m = _POSTCODE_RE.search(body)
        postcode = entry.get("postal_code") or (postcode_m.group(1) if postcode_m else None)

        photos: list[str] = []
        seen_p: set[str] = set()
        for img in soup.select("img[src*='img.netty.immo']"):
            src = img.get("src", "")
            if not src or src in seen_p:
                continue
            if "original" not in src:
                src = re.sub(r"_(?:thumb|medium|large|small)\.", "_original.", src)
            if src not in seen_p:
                seen_p.add(src)
                photos.append(src)

        if not title and not rent:
            return None

        return RawListing(
            source=self.source,
            external_id=entry["external_id"],
            url=url,
            title=title,
            description=_find_description(soup),
            rent_cc=rent,
            surface_m2=surface,
            rooms=rooms,
            property_type=f"T{rooms}" if rooms else None,
            postal_code=postcode,
            photo_urls=photos[:15],
            poster_type="agency",
        )


def card_postcode(anchor) -> str | None:
    """Postcode from the listing card wrapping ``anchor`` ("75014 - Paris")."""
    node = anchor
    for _ in range(6):
        m = _CARD_POSTCODE_RE.search(node.get_text(" ", strip=True))
        if m:
            return m.group(1)
        if node.parent is None:
            break
        node = node.parent
        if len(node.get_text(" ", strip=True)) > 400:
            break
    return None


async def _fetch(url: str) -> str:
    async with httpx.AsyncClient(headers=_HEADERS, timeout=20.0, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text


def _find_description(soup: BeautifulSoup) -> str | None:
    best = ""
    for el in soup.select("p, div"):
        text = el.get_text(" ", strip=True)
        if len(text) > len(best) and len(text) > 80:
            low = text.lower()
            if not any(kw in low for kw in ("cookie", "rgpd", "©", "tous droits", "javascript")):
                best = text
    return best or None

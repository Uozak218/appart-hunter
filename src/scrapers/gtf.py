"""GTF Locations scraper — small Paris boutique agency (gtf.fr).

~20 active listings. Server-rendered HTML, no anti-bot, no location filter.
Listings at ``/fr/louer-liste-des-biens``, details at
``/fr/bien-immobilier-<city-slug>-...-{id}``. When the slug carries a postcode
(``…-neuilly-sur-seine-92200-…``) we tag the entry with it so the base zone
pre-filter can skip it without a detail fetch.
2026-10-14: stock is Paris / Boulogne / Neuilly / Saint-Mandé only — nothing in
the 92-nord / 93 towns.
"""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from src.schemas.listing import RawListing
from src.scrapers._common import parse_price, parse_surface, text_of
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

GTF_BASE = "https://www.gtf.fr"
GTF_LISTING_URL = f"{GTF_BASE}/fr/louer-liste-des-biens"

_DETAIL_RE = re.compile(r"/fr/bien-immobilier-.*?-(\d{3,})$")
# Any French postcode (01xxx–98xxx); the look-behind skips refs like "A-05073".
_POSTCODE_RE = re.compile(r"(?<![\d-])((?:0[1-9]|[1-8]\d|9[0-8])\d{3})(?!\d)")
_SLUG_POSTCODE_RE = re.compile(r"-((?:0[1-9]|[1-8]\d|9[0-8])\d{3})-")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


class GtfScraper(BaseScraper):
    source = "gtf"
    use_http_only = True

    async def fetch_listing_page(self, page) -> list[dict[str, str]]:  # noqa: ARG002
        logger.info(f"[gtf] fetching listing {GTF_LISTING_URL}")
        try:
            html = await _fetch(GTF_LISTING_URL)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[gtf] listing fetch failed: {exc}")
            return []

        soup = BeautifulSoup(html, "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()

        for a in soup.select("a[href*='/fr/bien-immobilier-']"):
            href = a.get("href", "")
            m = _DETAIL_RE.search(href)
            if not m:
                continue
            ext_id = m.group(1)
            if ext_id in seen:
                continue
            seen.add(ext_id)
            full_url = href if href.startswith("http") else f"{GTF_BASE}{href}"
            entry = {"external_id": ext_id, "url": full_url}
            pc = _SLUG_POSTCODE_RE.search(href)
            if pc:
                entry["postal_code"] = pc.group(1)
            entries.append(entry)

        return entries

    async def parse_detail(self, page, entry: dict[str, str]) -> RawListing | None:  # noqa: ARG002
        url = entry["url"]
        try:
            html = await _fetch(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[gtf] detail fetch failed {url}: {exc}")
            return None

        soup = BeautifulSoup(html, "lxml")
        body = soup.get_text(" ", strip=True)
        title = text_of(soup.select_one("h1")) or text_of(soup.select_one("h2"))

        rent_m = re.search(r"([\d\s .,]+)\s*€\s*/\s*mois", body)
        rent = parse_price(rent_m.group(0)) if rent_m else parse_price(body)

        surface = parse_surface(body)
        rooms_m = re.search(r"(\d+)\s*pièce", body, re.I) or re.search(r"\bT(\d)\b", title, re.I)
        rooms = int(rooms_m.group(1)) if rooms_m else None
        if rooms is None and re.search(r"\bstudio\b", title + " " + body, re.I):
            rooms = 1

        # The listing's own postcode comes first ("NEUILLY SUR SEINE (92200)");
        # the footer carries the agency's (75009).
        postcode_m = _POSTCODE_RE.search(body)
        postcode = entry.get("postal_code") or (postcode_m.group(1) if postcode_m else None)

        furnished = None
        if "meuble" in url.lower() or re.search(r"\bmeubl[ée]\b", body, re.I):
            furnished = True
        if re.search(r"\bnon[- ]?meubl[ée]\b", body, re.I):
            furnished = False

        photos: list[str] = []
        seen_p: set[str] = set()
        for img in soup.select("img[src^='http']"):
            src = img.get("src", "")
            if not src or src in seen_p:
                continue
            if any(kw in src.lower() for kw in ("logo", "favicon", "icon", "banner", ".svg")):
                continue
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
            furnished=furnished,
            postal_code=postcode,
            photo_urls=photos[:15],
            poster_type="agency",
        )


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

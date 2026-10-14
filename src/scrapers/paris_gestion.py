"""Paris Gestion Immobilier scraper.

WordPress site with custom ``property`` post type. ~14 active non-meublé
rentals at peak, concentrated in 75013 (Gobelins/Salpêtrière/Vincent Auriol),
75014 (Montparnasse/Alésia), 75011 (Saint-Ambroise), 75012 (Bercy),
75018 (Lamarck-Montmartre side), 75005. Pile dans la cible.

Static HTML, zéro anti-bot → HTTP-only (no Chromium, no Scrapfly).
"""

from __future__ import annotations

import contextlib
import re

import httpx
from bs4 import BeautifulSoup

from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

PGI_LISTING_URL = "https://www.paris-gestion-immobilier.com/property/"
PGI_BASE = "https://www.paris-gestion-immobilier.com"

# Detail URL example:
#   /property/appartement-location-appartement-142-boulevard-vincent-auriol-75013-paris/
_DETAIL_RE = re.compile(
    r"/property/(appartement-location-appartement-[a-z0-9-]+?-(75\d{3})-paris)/?",
    re.I,
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}


class ParisGestionScraper(BaseScraper):
    source = "paris_gestion"
    use_http_only = True

    async def fetch_listing_page(self, page) -> list[dict[str, str]]:  # noqa: ARG002
        logger.info(f"[paris_gestion] fetching {PGI_LISTING_URL}")
        try:
            async with httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True) as c:
                resp = await c.get(PGI_LISTING_URL)
                resp.raise_for_status()
                html = resp.text
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[paris_gestion] fetch failed: {exc}")
            return []
        soup = BeautifulSoup(html, "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in soup.select('a[href*="/property/appartement-location-"]'):
            href = a.get("href") or ""
            m = _DETAIL_RE.search(href)
            if not m:
                continue
            slug, postcode = m.group(1), m.group(2)
            # Slug can get long (>100 chars) — DB external_id column is bounded.
            ext = slug[:80]
            if ext in seen:
                continue
            seen.add(ext)
            if href.startswith("/"):
                href = PGI_BASE + href
            entries.append({"external_id": ext, "url": href, "postal_code": postcode})
        return entries

    async def parse_detail(self, page, entry: dict[str, str]) -> RawListing | None:  # noqa: ARG002
        url = entry["url"]
        try:
            async with httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True) as c:
                resp = await c.get(url)
                resp.raise_for_status()
                html = resp.text
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[paris_gestion] detail fetch failed {url}: {exc}")
            return None
        soup = BeautifulSoup(html, "lxml")

        # Skip "Loué" — only keep available listings. The status badge is a
        # `<span class="label status-color-NN">Loué|Disponible</span>`.
        for label in soup.select("span.label, .property-label"):
            if "Loué" in label.get_text(strip=True):
                logger.debug(f"[paris_gestion] skipping rented listing {url}")
                return None

        # Skip if type explicitly meublé.
        body_text = soup.get_text(" ", strip=True)
        if re.search(r"Type\s+de\s+location\s*[:\-]?\s*meubl", body_text, re.I):
            logger.debug(f"[paris_gestion] skipping meublé listing {url}")
            return None

        # Use the generic helper for title/description/surface/rooms/postcode
        # /photos — those work fine on this WP theme.
        raw = parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=PGI_BASE
        )

        # Per-site price override: the Houzez WP theme renders the actual rent
        # in ``.item-price``. The currency symbol is mis-configured as ``$``
        # (instead of ``€``) so the generic ``parse_price`` (which requires €)
        # would otherwise fall back to the first € in the body — which is the
        # DPE cost (38 €) or the deposit (1292 €), not the rent.
        price_el = soup.select_one(".item-price, .property-price-value")
        if price_el:
            price_txt = price_el.get_text(" ", strip=True)
            # Strip currency symbols (€, $) and parse French-style number.
            m = re.search(r"(\d[\d\s ,.]*)", price_txt)
            if m:
                num = re.sub(r"[\s ]", "", m.group(1))
                # If comma is followed by ≤2 digits it's decimal; else thousand sep.
                # Examples: "1,330" → 1330 ; "1,330.50" → 1330.50 ; "1330,5" → 1330.5
                if "," in num and "." in num:
                    # Both present: dot is decimal (en) OR comma is decimal (fr).
                    # Houzez uses en convention with "$1,330.50" — drop comma.
                    cleaned = num.replace(",", "")
                elif "," in num:
                    # Comma alone: if 3 digits after → thousand sep, else decimal.
                    after = num.rsplit(",", 1)[1]
                    cleaned = num.replace(",", "") if len(after) == 3 else num.replace(",", ".")
                elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", num):
                    cleaned = num.replace(".", "")
                else:
                    cleaned = num
                with contextlib.suppress(ValueError):
                    raw.rent_cc = float(cleaned)
        return raw

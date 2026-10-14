"""Foncia scraper — curl_cffi + iproyal residential (HTTP-only).

Historique :
- 2026-04 : Foncia était friendlier (httpx + Playwright stealth marchaient).
- 2026-05-19 : Foncia a activé un anti-bot agressif (TLS + IP fingerprint).
  Playwright stealth → 403. Scrapfly ASP → 0 bytes silencieux.
  ``curl_cffi`` + iproyal résidentiel → HTTP 200 fiable.

On utilise donc ``use_http_only = True`` (zéro Chromium) et on tape via
``src.scrapers.cffi_client``. Le statut meublé est dérivé du **pattern URL**
(``/appartement/`` vide vs ``/appartement-meuble/`` meublé) — déterministe
et plus fiable que de scanner le titre/description.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from src.schemas.listing import RawListing
from src.scrapers.base import BaseScraper
from src.scrapers.cffi_client import fetch_html
from src.utils.logging import logger

FONCIA_LISTING_URL = "https://fr.foncia.com/location/paris-75"
FONCIA_BASE = "https://fr.foncia.com"

# Foncia paginates at ``/location/paris-75/page-N`` (page 1 == the base URL).
# The first page only holds ~10 of 50+ Paris rentals, so we sweep pages until
# one yields no *new* listing (Foncia clamps an over-range page-N to the last
# real page and re-serves it) or we hit this safety cap.
_MAX_LISTING_PAGES = 15

# Detail URL: /location/paris-750XX/appartement/123456789.htm
#         or  /location/paris-750XX/appartement-meuble/123456789.htm
_DETAIL_RE = re.compile(r"/location/paris-(75\d{3})/(appartement(?:-meuble)?)/(\d{7,})\.htm")


class FonciaScraper(BaseScraper):
    source = "foncia"
    use_http_only = True

    async def fetch_listing_page(self, page) -> list[dict[str, str]]:  # noqa: ARG002
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        last_page = 0
        for page_num in range(1, _MAX_LISTING_PAGES + 1):
            url = FONCIA_LISTING_URL if page_num == 1 else f"{FONCIA_LISTING_URL}/page-{page_num}"
            logger.info(f"[foncia] fetching listing {url}")
            try:
                html = await fetch_html(url, referer="https://fr.foncia.com/")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[foncia] listing fetch failed {url}: {exc}")
                break
            soup = BeautifulSoup(html, "lxml")
            new_on_page = 0
            for a in soup.select("a[href*='/location/paris-']"):
                href = a.get("href") or ""
                m = _DETAIL_RE.search(href)
                if not m:
                    continue
                postcode, path_type, ext = m.group(1), m.group(2), m.group(3)
                if ext in seen:
                    continue
                seen.add(ext)
                new_on_page += 1
                full = FONCIA_BASE + href if href.startswith("/") else href
                entries.append(
                    {
                        "external_id": ext,
                        "url": full,
                        "postal_code": postcode,
                        # URL pattern is deterministic — pass it through so Sonnet
                        # gets the right ``listing.furnished`` and doesn't guess.
                        "furnished": path_type == "appartement-meuble",
                    }
                )
            last_page = page_num
            # No new listing on this page → we've run past the last real page
            # (Foncia clamps and re-serves it). Stop sweeping.
            if new_on_page == 0:
                break
        else:
            # Loop exhausted the cap without an empty page — there may be more.
            logger.warning(
                f"[foncia] hit page cap {_MAX_LISTING_PAGES}; some listings may be missed"
            )
        logger.info(f"[foncia] {len(entries)} listings across {last_page} page(s)")
        return entries

    async def parse_detail(self, page, entry: dict[str, str]) -> RawListing | None:  # noqa: ARG002
        url = entry["url"]
        try:
            html = await fetch_html(url, referer=FONCIA_BASE + "/location/paris-75")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[foncia] detail fetch failed {url}: {exc}")
            return None
        soup = BeautifulSoup(html, "lxml")
        title = _text(soup.select_one("h1"))
        description = _text(
            soup.select_one(".description, [class*='escription'], [itemprop='description']")
        )

        # Rent: prefer "1828.35 €/mois" pattern (Foncia title format), else
        # the first 4-digit € amount in the body (skips DPE 38 €).
        rent_m = re.search(r"(\d+(?:[.,]\d+)?)\s*€/mois", html)
        if rent_m:
            rent = float(rent_m.group(1).replace(",", "."))
        else:
            rent = None
            for m in re.finditer(r"(\d[\d\s ]+)\s*€", soup.get_text(" ", strip=True)):
                raw = re.sub(r"[\s ]", "", m.group(1))
                try:
                    v = float(raw)
                    if 300 <= v <= 10000:
                        rent = v
                        break
                except ValueError:
                    pass

        # Surface from title: "...66.67 m²"
        surf_m = re.search(r"(\d+(?:[.,]\d+)?)\s*m\s*²", title + " " + html)
        surface = float(surf_m.group(1).replace(",", ".")) if surf_m else None

        # Rooms — "3 pièces" / "T3" / "Studio"
        rooms = None
        rm = re.search(r"(\d)\s*pi[èe]ces?", title, re.I) or re.search(r"\bT\s?(\d)\b", title, re.I)
        if rm:
            rooms = int(rm.group(1))
        elif re.search(r"\bstudio\b", title, re.I):
            rooms = 1

        postcode = entry.get("postal_code")
        # ``furnished`` decided at listing time from URL pattern.
        furnished = entry.get("furnished")

        photos = []
        for img in soup.select(".gallery img, [class*='hoto'] img, picture img, img")[:30]:
            s = img.get("data-src") or img.get("src") or ""
            if s.startswith("http") and ("cloudfront" in s or "foncia" in s):
                photos.append(s)
        # Dedup keeping order.
        seen_p: set[str] = set()
        photos = [p for p in photos if not (p in seen_p or seen_p.add(p))][:15]

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
            furnished=furnished,
            postal_code=postcode,
            photo_urls=photos,
        )


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""

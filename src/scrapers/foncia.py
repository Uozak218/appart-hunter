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
from typing import Any

from bs4 import BeautifulSoup

from src.config import ZoneRule
from src.schemas.listing import RawListing
from src.scrapers._common import parse_price, parse_surface
from src.scrapers.base import BaseScraper
from src.scrapers.bienici import passes_card_filters, unique_towns
from src.scrapers.cffi_client import fetch_html
from src.utils.hashing import normalise_text
from src.utils.logging import logger

FONCIA_BASE = "https://fr.foncia.com"
# One search per commune of ``criteria.zones.include`` (deduped by INSEE):
#   /location/<ville>-<code postal>/appartement   (e.g. clichy-92110)
# Foncia keys the page on the commune — saint-denis-93200 and saint-denis-93210
# serve the same results. No price/surface/rooms filter is exposed in the URL,
# so those are applied on the listing cards (``_should_fetch_detail``).
FONCIA_TOWN_URL = FONCIA_BASE + "/location/{town}-{postcode}/appartement"

# Pagination: ``<town url>/page-N`` (page 1 == the base URL). Sweep until a
# page yields no *new* listing (Foncia clamps an over-range page-N to the last
# real page and re-serves it) or we hit this per-town safety cap.
_MAX_LISTING_PAGES = 5

# Detail URLs:
#   /location/levallois-perret-92300/appartement/331699284.htm
#   /location/levallois-perret-92300/appartement/30089-552.htm
#   /location/la-plaine-st-denis-93210/appartement-meuble/331695563.htm
#   /location/saint-denis-93/appartement/331711066.htm   (department only!)
_DETAIL_RE = re.compile(
    r"/location/[a-z0-9-]+?-(\d{5}|\d{2}|2[ab])/"
    r"((?:appartement|studio|maison)(?:-meuble)?)/([\w-]+)\.htm"
)
_CITY_POSTCODE_RE = re.compile(r"\((\d{5})\)")
_ANY_POSTCODE_RE = re.compile(r"\b(\d{5})\b")


def town_url(rule: ZoneRule) -> str:
    return FONCIA_TOWN_URL.format(
        town=normalise_text(rule.city).replace(" ", "-"), postcode=rule.code
    )


def parse_listing(html: str) -> list[dict[str, Any]]:
    """Listing page → entries, with card-level rent/surface/rooms/postcode."""
    soup = BeautifulSoup(html, "lxml")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in soup.select("a[href*='/location/']"):
        href = a.get("href") or ""
        m = _DETAIL_RE.search(href)
        if not m:
            continue
        loc, path_type, ext = m.group(1), m.group(2), m.group(3)
        if ext in seen:
            continue
        seen.add(ext)
        card, text = a, ""
        for _ in range(8):
            if card.parent is None:
                break
            card = card.parent
            text = card.get_text(" ", strip=True)
            if "€" in text and "m²" in text:
                break
        postcode = loc if len(loc) == 5 else _first(_CITY_POSTCODE_RE, text)
        entries.append(
            {
                "external_id": ext,
                "url": FONCIA_BASE + href if href.startswith("/") else href,
                "postal_code": postcode,
                # URL pattern is deterministic — pass it through so Sonnet
                # gets the right ``listing.furnished`` and doesn't guess.
                "furnished": path_type.endswith("-meuble"),
                "rent_cc": _card_price(card),
                "surface_m2": parse_surface(text),
                "rooms": _parse_rooms(text),
            }
        )
    return entries


class FonciaScraper(BaseScraper):
    source = "foncia"
    use_http_only = True

    async def fetch_listing_page(self, page) -> list[dict[str, Any]]:  # noqa: ARG002
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for rule in unique_towns(self.criteria):
            if not rule.city:
                logger.warning(f"[foncia] no city name for {rule.code}; skipped")
                continue
            base_url = town_url(rule)
            for page_num in range(1, _MAX_LISTING_PAGES + 1):
                url = base_url if page_num == 1 else f"{base_url}/page-{page_num}"
                logger.info(f"[foncia] fetching listing {url}")
                try:
                    html = await fetch_html(url, referer=FONCIA_BASE + "/")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[foncia] listing fetch failed {url}: {exc}")
                    break
                new_on_page = 0
                for e in parse_listing(html):
                    if e["external_id"] in seen:
                        continue
                    seen.add(e["external_id"])
                    new_on_page += 1
                    entries.append(e)
                if new_on_page == 0:
                    break
            else:
                logger.warning(f"[foncia] hit page cap for {rule.city}; some listings missed")
        logger.info(f"[foncia] {len(entries)} listings")
        return entries

    def _should_fetch_detail(self, entry: dict[str, Any]) -> bool:
        return passes_card_filters(self.criteria, entry)

    async def parse_detail(self, page, entry: dict[str, Any]) -> RawListing | None:  # noqa: ARG002
        url = entry["url"]
        try:
            html = await fetch_html(url, referer=FONCIA_BASE + "/location")
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

        postcode = (
            entry.get("postal_code")
            or _first(_ANY_POSTCODE_RE, title)
            or _first(_CITY_POSTCODE_RE, soup.get_text(" ", strip=True))
        )
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


def _first(pattern: re.Pattern[str], text: str | None) -> str | None:
    m = pattern.search(text or "")
    return m.group(1) if m else None


def _parse_rooms(text: str) -> int | None:
    m = re.search(r"(\d+)\s*pi[èe]ces?", text or "", re.I)
    if m:
        return int(m.group(1))
    return 1 if re.search(r"\bstudio\b", text or "", re.I) else None


def _card_price(card) -> float | None:
    """First text node carrying a € amount — parsing the flattened card text
    would glue the photo counter ("x7") onto the price ("7 961 €")."""
    for t in card.stripped_strings:
        if "€" in t:
            return parse_price(t)
    return None

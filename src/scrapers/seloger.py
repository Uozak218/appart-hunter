"""SeLoger scraper (skeleton to flesh out once CSS selectors are validated).

SeLoger sits behind Cloudflare. Like LBC we rely on the hydrated Next/Nuxt
JSON blob embedded in the page. The CSS selectors here are best-guess
fallbacks — re-check them the first time you run against prod and tune in
``parse_detail``. When the JSON path breaks, the scraper raises, the health
tracker flags it, and the notifier alerts "scraper seloger down".
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.config import get_criteria
from src.schemas.listing import RawListing
from src.scrapers.base import BaseScraper
from src.utils.geo import arrondissement_from_postcode, is_in_target_zone
from src.utils.logging import logger

# Note: SeLoger's ``places`` parameter uses literal braces (``[{ci:750056}]``)
# that clash with Python str.format. We escape them by doubling the braces.
SELOGER_LISTING_URL = (
    "https://www.seloger.com/list.htm?"
    "projects=1&types=1,2&places=[{{ci:750056}}]&price=NaN/{max_price}&"
    "surface={min_surface}/NaN&enterprise=0&natures=1,2,4&"
    "sort=d_dt_crea&qsVersion=1.0"
)

_NUXT_RE = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# SeLoger detail URLs carry the arrondissement: '.../paris-11eme-75/123.htm'.
_SELOGER_ARR_RE = re.compile(r"/paris-(\d{1,2})(?:er|eme|e)?-75/")


def postcode_from_seloger_url(url: str) -> str | None:
    """'/.../paris-11eme-75/123.htm' -> '75011'. None if not parseable."""
    m = _SELOGER_ARR_RE.search(url or "")
    if not m:
        return None
    arr = int(m.group(1))
    return f"750{arr:02d}" if 1 <= arr <= 20 else None


class SeLogerScraper(BaseScraper):
    source = "seloger"
    # DataDome — fetch via Scrapfly. SeLoger hydrates results client-side.
    use_scrapfly = True
    scrapfly_render_js = True

    def _should_fetch_detail(self, entry: dict[str, Any]) -> bool:
        # SeLoger's listing page only gives id + url, so the only thing we can
        # reject before paying for the detail is the zone — and the arrondissement
        # is in the URL. Skips the excluded arrondissements (8/15/16/19).
        postcode = postcode_from_seloger_url(entry.get("url", ""))
        if postcode is None:
            return True
        if not is_in_target_zone(self.criteria, postcode=postcode, lat=None, lon=None):
            logger.debug(
                f"[seloger] skip detail {entry.get('external_id')}: hors zone ({postcode})"
            )
            return False
        return True

    async def fetch_listing_page(self, page: Page) -> list[dict[str, Any]]:
        c = self.criteria or get_criteria()
        min_surface = int(min(c.property.min_surface.values()) if c.property.min_surface else 40)
        url = SELOGER_LISTING_URL.format(
            max_price=int(c.property.max_rent_cc), min_surface=min_surface
        )
        logger.info(f"[seloger] fetching listing {url}")
        await page.goto(url, timeout=self.listing_timeout_ms, wait_until="domcontentloaded")

        # Cloudflare interstitial? give it a sec.
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=8_000)

        html = await page.content()
        m = _NUXT_RE.search(html)
        if m:
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                data = None
            entries = _walk_for_seloger_items(data) if data else []
            if entries:
                return entries

        # HTML fallback — SeLoger no longer exposes __NEXT_DATA__ (2026 redesign).
        # Detail URLs: /annonces/locations/appartement/paris-XXeme-75/NNNNNNNNN.htm
        soup = BeautifulSoup(html, "lxml")
        entries = []
        seen: set[str] = set()
        for a in soup.select("a[href*='/annonces/locations/']"):
            href = a.get("href") or ""
            ext = _extract_id_from_url(href)
            if not ext or ext in seen:
                continue
            # Keep only Paris (75) — some listings are in nearby départements.
            if "-75/" not in href:
                continue
            seen.add(ext)
            # Strip query string + relative-resolve.
            href = href.split("?", 1)[0]
            if href.startswith("/"):
                href = "https://www.seloger.com" + href
            entries.append({"external_id": str(ext), "url": href})
        logger.debug(f"[seloger] parsed {len(entries)} entries from HTML")
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, Any]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[seloger] detail nav failed {url}: {exc}")
            return None

        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=8_000)
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)

        rent = _extract_rent(text)
        surface = _extract_surface(text)
        rooms = _extract_rooms(text)

        postcode_match = re.search(r"\b(75\d{3})\b", html)
        postcode = postcode_match.group(1) if postcode_match else None
        arr = arrondissement_from_postcode(postcode)

        page_title = soup.find("title")
        title = (
            page_title.get_text(strip=True).split(" - SeLoger")[0].strip()
            if page_title
            else ""
        ) or f"Appartement {('T' + str(rooms) + ' ') if rooms else ''}Paris {arr}e".strip()
        description = _text(soup.select_one("[class*='escription'], [data-test*='escription']"))

        photos: list[str] = []
        for img in soup.select("img[src], img[data-src]"):
            src = (img.get("src") or img.get("data-src") or "").strip()
            if src.startswith("http") and any(k in src for k in ("seloger", "/photos", "cdn")):
                photos.append(src)
            if len(photos) >= 20:
                break

        return RawListing(
            source=self.source,
            external_id=str(entry["external_id"]),
            url=url,
            title=title,
            description=description,
            rent_cc=rent,
            surface_m2=surface,
            rooms=rooms,
            property_type=f"T{rooms}" if rooms else None,
            postal_code=postcode,
            photo_urls=photos,
            raw={"html_length": len(html)},
        )


def _walk_for_seloger_items(data: Any) -> list[dict[str, Any]]:
    """Best-effort: look for items with `idAnnonce` and `permalink` fields."""
    found: list[dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            pid = node.get("idAnnonce") or node.get("id")
            perma = node.get("permalink") or node.get("url")
            if pid and perma and isinstance(perma, str) and "/annonces/" in perma:
                href = perma if perma.startswith("http") else f"https://www.seloger.com{perma}"
                found.append({"external_id": str(pid), "url": href})
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(data)
    return found


def _text(node) -> str:
    return node.get_text(strip=True) if node else ""


def _parse_price(text: str) -> float | None:
    """Parse a French-formatted euro price (point=thousands, comma=decimal)."""
    if not text:
        return None
    m = re.search(r"([\d\s\u00a0.,]+)\s*€", text)
    if not m:
        return None
    raw = re.sub(r"[\s\u00a0]", "", m.group(1))
    if "," in raw:
        cleaned = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        cleaned = raw.replace(".", "")
    else:
        cleaned = raw
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_surface(text: str) -> float | None:
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m", text)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


# SeLoger's 2026 redesign dropped stable CSS hooks for price/surface/rooms, so we
# pull them from the rendered page text instead, anchored on rent-specific markers.
def _extract_rent(text: str) -> float | None:
    m = re.search(r"([\d][\d\s .,]{0,12}?)\s*€\s*/\s*mois", text)
    if m is None:
        m = re.search(r"([\d][\d\s .,]{0,12}?)\s*€\s*(?:CC|charges)", text)
    return _parse_price(f"{m.group(1)} €") if m else None


def _extract_surface(text: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m[²2]", text)
    return float(m.group(1).replace(",", ".")) if m else None


def _extract_rooms(text: str) -> int | None:
    m = re.search(r"\bT(\d)\b", text) or re.search(r"(\d+)\s*pi[eè]ce", text)
    return int(m.group(1)) if m else None


def _extract_id_from_url(url: str) -> str | None:
    m = re.search(r"/(\d{7,})/?", url)
    return m.group(1) if m else None

"""Superimmo scraper.

Superimmo.com aggregates rental listings from multiple agencies. One listing
URL per commune of ``criteria.zones.include`` (deduped by INSEE):

    /location/appartement/ile-de-france/<département>/<ville>-<code postal>
        ?prix-max=<max_rent>&surface-min=<min_surface>

(format observed on the site's own links, e.g.
``/location/appartement/ile-de-france/hauts-de-seine/antony-92160``). The
room-count path segment (``/pieces-1-2``) exists on the site but its
combination with a town page is unverified, so rooms are filtered on the cards.

Since 2026-10 the site sits behind a Cloudflare Turnstile "Vérification" page
that headless Chromium does not pass; the scraper detects it and returns no
entries instead of parsing the challenge page.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.config import SearchCriteria, ZoneRule
from src.schemas.listing import RawListing
from src.scrapers._common import parse_price, parse_surface
from src.scrapers.base import BaseScraper
from src.scrapers.bienici import passes_card_filters, unique_towns
from src.utils.hashing import normalise_text
from src.utils.logging import logger

SUPERIMMO_BASE = "https://www.superimmo.com"
SUPERIMMO_LISTING_URL = (
    SUPERIMMO_BASE + "/location/appartement/{region}/{department}/{town}"
    "?prix-max={max_price}&surface-min={min_surface}"
)

# Île-de-France départements → Superimmo path slugs. Towns elsewhere are
# skipped with a warning (extend the map + region if ever needed).
_IDF_DEPARTMENTS = {
    "75": "paris",
    "77": "seine-et-marne",
    "78": "yvelines",
    "91": "essonne",
    "92": "hauts-de-seine",
    "93": "seine-saint-denis",
    "94": "val-de-marne",
    "95": "val-d-oise",
}

# /annonces/location-appartement-29m-levallois-perret-92300-x128zj9
_DETAIL_ID_RE = re.compile(r"-([a-z0-9]{6,})/?$")
_URL_POSTCODE_RE = re.compile(r"-(\d{5})-[a-z0-9]{6,}/?$")
_CITY_POSTCODE_RE = re.compile(r"\((\d{5})\)")
_ANY_POSTCODE_RE = re.compile(r"\b(\d{5})\b")


def slugify(text: str) -> str:
    """'Asnières-sur-Seine' → 'asnieres-sur-seine'."""
    return normalise_text(text).replace(" ", "-")


def listing_url(criteria: SearchCriteria, rule: ZoneRule) -> str | None:
    dept = _IDF_DEPARTMENTS.get(rule.code[:2])
    if not dept or not rule.city:
        return None
    p = criteria.property
    return SUPERIMMO_LISTING_URL.format(
        region="ile-de-france",
        department=dept,
        town=f"{slugify(rule.city)}-{rule.code}",
        max_price=int(p.max_rent_cc),
        min_surface=int(min(p.min_surface.values())) if p.min_surface else 20,
    )


def postcode_from_url(url: str) -> str | None:
    m = _URL_POSTCODE_RE.search(url or "")
    return m.group(1) if m else None


def postcode_from_text(text: str) -> str | None:
    m = _CITY_POSTCODE_RE.search(text or "") or _ANY_POSTCODE_RE.search(text or "")
    return m.group(1) if m else None


def is_challenge(html: str) -> bool:
    return "challenges.cloudflare.com/turnstile" in html or "<title>Vérification" in html


async def _content_past_challenge(page: Page, timeout_ms: int = 25_000) -> str:
    """Page HTML; on a Turnstile page, give the managed challenge a chance to
    auto-solve (it submits its form and navigates) before giving up."""
    html = await page.content()
    if not is_challenge(html):
        return html
    try:
        await page.wait_for_function(
            "() => !document.title.startsWith('Vérification')", timeout=timeout_ms
        )
        await page.wait_for_load_state("domcontentloaded")
    except Exception:  # noqa: BLE001
        return html
    return await page.content()


class SuperImmoScraper(BaseScraper):
    source = "superimmo"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for rule in unique_towns(self.criteria):
            url = listing_url(self.criteria, rule)
            if not url:
                logger.warning(f"[superimmo] no URL for {rule.city} ({rule.code}); skipped")
                continue
            logger.info(f"[superimmo] fetching listing {url}")
            try:
                await page.goto(url, timeout=self.listing_timeout_ms, wait_until="domcontentloaded")
                html = await _content_past_challenge(page)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[superimmo] nav failed: {exc}")
                continue
            if is_challenge(html):
                logger.warning("[superimmo] blocked by Turnstile challenge; aborting cycle")
                break
            if "Page introuvable" in html[:5000]:
                logger.warning(f"[superimmo] unknown town slug for {rule.city}: {url}")
                continue
            for e in parse_cards(html):
                if e["external_id"] in seen:
                    continue
                seen.add(e["external_id"])
                entries.append(e)
        return entries

    def _should_fetch_detail(self, entry: dict[str, Any]) -> bool:
        return passes_card_filters(self.criteria, entry)

    async def parse_detail(self, page: Page, entry: dict[str, Any]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[superimmo] detail nav failed {url}: {exc}")
            return None
        html = await _content_past_challenge(page)
        if is_challenge(html):
            logger.warning(f"[superimmo] detail blocked by Turnstile {url}")
            return None
        soup = BeautifulSoup(html, "lxml")
        title = (soup.select_one("h1") or soup.new_tag("h1")).get_text(strip=True)
        description = _text(
            soup.select_one(".description, [itemprop='description'], .annonce-description")
        )
        price_text = _text(soup.select_one(".price, [itemprop='price'], .annonce-price"))
        body_text = soup.get_text(" ", strip=True)

        rent = parse_price(price_text) or entry.get("rent_cc") or parse_price(body_text)
        surface = entry.get("surface_m2") or parse_surface(title) or parse_surface(body_text)
        rooms = entry.get("rooms") or _parse_rooms(title + " " + body_text)
        postcode = (
            postcode_from_url(url) or entry.get("postal_code") or postcode_from_text(title)
        ) or postcode_from_text(body_text)
        photos = [
            img.get("data-src") or img.get("src")
            for img in soup.select(".carousel img, .gallery img, .photos img")[:15]
            if img.get("data-src") or img.get("src")
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
            postal_code=postcode,
            photo_urls=[p for p in photos if p],
        )


def parse_cards(html: str) -> list[dict[str, Any]]:
    """Listing page → entries, with card-level rent/surface/rooms/postcode."""
    soup = BeautifulSoup(html, "lxml")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in soup.select("a[href*='/annonces/location-']"):
        href = a.get("href")
        if not href:
            continue
        if href.startswith("/"):
            href = SUPERIMMO_BASE + href
        m = _DETAIL_ID_RE.search(href)
        if not m:
            continue
        ext = m.group(1)
        if ext in seen:
            continue
        seen.add(ext)
        # Climb to the card container to read its summary line
        # ("812 € CC | Appartement • 29 m² • 1 pièce | Clichy (92110)").
        card, text = a, ""
        for _ in range(8):
            if card.parent is None:
                break
            card = card.parent
            text = card.get_text(" ", strip=True)
            if "€" in text and "m²" in text:
                break
        entries.append(
            {
                "external_id": ext,
                "url": href,
                "postal_code": postcode_from_url(href) or _city_postcode(text),
                "rent_cc": _card_price(card),
                "surface_m2": parse_surface(text),
                "rooms": _parse_rooms(text),
            }
        )
    return entries


def _city_postcode(text: str) -> str | None:
    m = _CITY_POSTCODE_RE.search(text or "")
    return m.group(1) if m else None


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _parse_rooms(text: str) -> int | None:
    m = re.search(r"(\d+)\s*pi[èe]ces?", text or "", re.I) or re.search(
        r"\bT\s?(\d)\b", text or "", re.I
    )
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

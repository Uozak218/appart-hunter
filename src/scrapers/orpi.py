"""Orpi scraper.

Largest French agency network. Searches go through ``/recherche`` with one
``locations[i][value]=<city-slug>`` per target town (built from
``criteria.zones.include``) plus price / surface / rooms filters. The page is
server-rendered: the filtered results live in the JSON attribute
``div[data-component="estate-result"][data-result]`` (``items``); the
``similars`` key and the ``<a>`` cards rendered next to it are *suggestions*
(sales, other towns, over-budget) and must be ignored.

Orpi city slugs are plain slugified names, except for homonyms and renamed
towns: "saint-denis" resolves to La Réunion (97400) and Saint-Denis (93) is
"saint-denis-seine-saint-denis"; "saint-ouen-sur-seine" is unknown and the
93400 town is "saint-ouen". We therefore send a few slug candidates per town
(unknown slugs are ignored by Orpi), read back how Orpi resolved them
(``data-component="searchbar"`` → ``data-search``) and drop candidates that
resolved to a postcode outside the zone list, refetching once.

Detail pages embed the full estate as JSON in
``[data-component="estate-poi-map"][data-estate]``.

URL + JSON validated against the live site on 2026-10-14.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.config import SearchCriteria, ZoneRule
from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

ORPI_BASE = "https://www.orpi.com"
ORPI_SEARCH_URL = f"{ORPI_BASE}/recherche"

# Département slugs Orpi appends to disambiguate homonyms
# (e.g. "saint-denis-seine-saint-denis"). Île-de-France is enough for now.
_DEPT_SLUGS = {
    "75": "paris",
    "77": "seine-et-marne",
    "78": "yvelines",
    "91": "essonne",
    "92": "hauts-de-seine",
    "93": "seine-saint-denis",
    "94": "val-de-marne",
    "95": "val-d-oise",
}
# Postcode encoded in the detail slug: /annonce-location-appartement-t2-colombes-92700-<uuid>/
_SLUG_POSTCODE_RE = re.compile(r"-(\d{5})-[a-f0-9]{8}-")
_UUID_RE = re.compile(r"-([a-f0-9]{8}-[a-f0-9-]{12,})/?$")

# Slug candidates that Orpi resolved to an out-of-zone town (e.g. "saint-denis"
# → 97400). Remembered for the process lifetime so we only pay the refetch once.
_REJECTED_SLUGS: set[str] = set()


def slugify(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")


def target_towns(criteria: SearchCriteria) -> list[ZoneRule]:
    """Zone rules with a city name, deduped by INSEE code (fallback: city)."""
    seen: set[str] = set()
    towns: list[ZoneRule] = []
    for rule in criteria.zones.include:
        if not rule.city:
            continue
        key = rule.insee or slugify(rule.city)
        if key in seen:
            continue
        seen.add(key)
        towns.append(rule)
    return towns


def slug_candidates(rule: ZoneRule) -> list[str]:
    base = slugify(rule.city)
    out = [base]
    short = re.sub(r"-sur-[a-z-]+$", "", base)
    if short != base:
        out.append(short)
    dept = _DEPT_SLUGS.get(rule.code[:2])
    if dept:
        out.append(f"{base}-{dept}")
    return out


def wanted_rooms(criteria: SearchCriteria) -> list[int]:
    rooms: set[int] = set()
    for t in [*criteria.property.types.preferred, *criteria.property.types.acceptable]:
        m = re.fullmatch(r"T(\d)", t.strip().upper())
        if m:
            rooms.add(int(m.group(1)))
    return sorted(rooms)


def min_surface(criteria: SearchCriteria) -> int | None:
    values = list(criteria.property.min_surface.values())
    return int(min(values)) if values else None


def build_search_url(criteria: SearchCriteria, exclude: set[str] | None = None) -> str:
    exclude = exclude or set()
    params: list[tuple[str, str]] = [
        ("transactions[0]", "rent"),
        ("realEstateTypes[0]", "appartement"),
    ]
    slugs: list[str] = []
    for rule in target_towns(criteria):
        for s in slug_candidates(rule):
            if s not in exclude and s not in slugs:
                slugs.append(s)
    params += [(f"locations[{i}][value]", s) for i, s in enumerate(slugs)]
    params.append(("maxPrice", str(int(criteria.property.max_rent_cc))))
    surface = min_surface(criteria)
    if surface:
        params.append(("minSurface", str(surface)))
    params += [(f"nbRooms[{i}]", str(r)) for i, r in enumerate(wanted_rooms(criteria))]
    params.append(("sort", "date-down"))
    return f"{ORPI_SEARCH_URL}?{urlencode(params)}"


def _json_attr(soup: BeautifulSoup, component: str, attr: str) -> dict[str, Any] | None:
    node = soup.select_one(f'[data-component="{component}"][{attr}]')
    if node is None:
        return None
    try:
        return json.loads(node.get(attr) or "")
    except ValueError:
        return None


def parse_search_results(html: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Return (items, resolved_locations, total_count) from a /recherche page."""
    soup = BeautifulSoup(html, "lxml")
    result = _json_attr(soup, "estate-result", "data-result") or {}
    search = _json_attr(soup, "searchbar", "data-search") or {}
    return (
        list(result.get("items") or []),
        list(search.get("locations") or []),
        int(result.get("count") or 0),
    )


def misresolved_slugs(locations: list[dict[str, Any]], wanted_codes: set[str]) -> set[str]:
    """Slugs Orpi resolved to a town whose postcode is not in the zone list."""
    bad: set[str] = set()
    for loc in locations:
        zips = {str(z) for z in (loc.get("zipcode") or []) if z}
        if zips and not zips & wanted_codes:
            bad.add(str(loc.get("value")))
    return bad


def item_to_entry(item: dict[str, Any]) -> dict[str, str] | None:
    slug = str(item.get("slug") or "")
    ext = str(item.get("reference") or "")
    if not ext:
        m = _UUID_RE.search(slug)
        ext = m.group(1) if m else ""
    if not slug or not ext:
        return None
    entry = {"external_id": ext, "url": f"{ORPI_BASE}/annonce-location-{slug}/"}
    m = _SLUG_POSTCODE_RE.search(f"-{slug}-")
    if m:
        entry["postal_code"] = m.group(1)
    tags = [str(t).lower() for t in item.get("tags") or []]
    if any(t.startswith("meubl") for t in tags):
        entry["furnished"] = "1"
    return entry


class OrpiScraper(BaseScraper):
    source = "orpi"

    def _should_fetch_detail(self, entry: dict[str, Any]) -> bool:
        # Furnished tag is on the search card — skip when we want unfurnished.
        return not (self.criteria.property.furnished is False and entry.get("furnished"))

    async def _load(self, page: Page, url: str) -> str | None:
        try:
            await page.goto(url, timeout=self.listing_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[orpi] nav failed: {exc}")
            return None
        return await page.content()

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        wanted = {r.code for r in self.criteria.zones.include}
        url = build_search_url(self.criteria, _REJECTED_SLUGS)
        logger.info(f"[orpi] fetching listing {url}")
        html = await self._load(page, url)
        if html is None:
            return []
        items, locations, count = parse_search_results(html)
        bad = misresolved_slugs(locations, wanted) - _REJECTED_SLUGS
        if bad:
            logger.info(f"[orpi] dropping out-of-zone slugs {sorted(bad)} and refetching")
            _REJECTED_SLUGS.update(bad)
            html = await self._load(page, build_search_url(self.criteria, _REJECTED_SLUGS))
            if html is None:
                return []
            items, locations, count = parse_search_results(html)
        resolved = {str(z) for loc in locations for z in (loc.get("zipcode") or [])}
        missing = sorted(wanted - resolved)
        if missing:
            logger.warning(f"[orpi] no Orpi location resolved for postcodes {missing}")
        if count > len(items):
            logger.info(f"[orpi] {count} results, only first {len(items)} parsed (page 1)")

        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in items:
            entry = item_to_entry(item)
            if entry is None or entry["external_id"] in seen:
                continue
            seen.add(entry["external_id"])
            entries.append(entry)
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[orpi] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        estate = _json_attr(soup, "estate-poi-map", "data-estate")
        if estate:
            return estate_to_listing(estate, entry=entry, url=url, source=self.source)
        return parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=ORPI_BASE
        )


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def estate_to_listing(
    estate: dict[str, Any], *, entry: dict[str, str], url: str, source: str = "orpi"
) -> RawListing:
    rooms = estate.get("nbRooms")
    rooms = int(rooms) if isinstance(rooms, int | float) else None
    features = {str(f).lower() for f in estate.get("comfortFeatures") or []}
    seo = estate.get("seo") or {}
    city = estate.get("locationDescription") or ""
    title = seo.get("metaTitle") or " ".join(
        p for p in ("Appartement", f"{rooms} pièces" if rooms else "", city) if p
    )
    postcode = estate.get("zipCode") or entry.get("postal_code")
    photos = [str(u) for u in (estate.get("imagesFull") or estate.get("images") or []) if u]
    story = estate.get("storyLocation")
    return RawListing(
        source=source,
        external_id=entry["external_id"],
        url=url,
        title=title,
        description=estate.get("longAd"),
        rent_cc=_num(estate.get("price")),
        charges=_num(estate.get("chargeReserve")),
        surface_m2=_num(estate.get("liveableSurface") or estate.get("surface")),
        rooms=rooms,
        property_type=f"T{rooms}" if rooms else None,
        furnished=True if "furnished" in features else None,
        floor=int(story) if isinstance(story, int) else None,
        has_elevator=estate.get("elevator") if isinstance(estate.get("elevator"), bool) else None,
        postal_code=str(postcode) if postcode else None,
        latitude=_num(estate.get("latitude")),
        longitude=_num(estate.get("longitude")),
        photo_urls=photos[:15],
        poster_type="agency",
    )

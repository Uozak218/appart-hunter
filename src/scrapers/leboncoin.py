"""LeBonCoin scraper.

LBC ships behind DataDome. The fastest and most reliable entry point is the
public locations search JSON embedded in ``__NEXT_DATA__`` of the listing
page — we navigate to the search URL with stealth, then read the hydrated
state directly. The detail page uses the same pattern.

The CSS selectors used as fallbacks WILL drift — the parser is defensive:
we try JSON first, BeautifulSoup + multiple selectors second, and surface
an error cleanly so the health-tracker alerts via Telegram.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

from playwright.async_api import Page

from src.config import SearchCriteria, get_criteria, get_settings
from src.schemas.listing import RawListing
from src.scrapers.base import BaseScraper
from src.utils.geo import is_in_target_zone
from src.utils.hashing import normalise_text
from src.utils.logging import logger

LBC_LISTING_URL = (
    "https://www.leboncoin.fr/recherche?"
    # furnished=2 → NON meublé (vide). furnished=1 means furnished, which is
    # the opposite of what we want — the previous value silently wasted every
    # Scrapfly credit on listings the Python prefilter then rejects.
    # owner_type=private → particuliers only: the unique no-fee stock (agency
    # ads are already covered by the agency scrapers + bienici), and fewer ads
    # = fewer Scrapfly detail calls per poll.
    "category=10&real_estate_type=2&owner_type=private&furnished=2&"
    "price=min-{max_price}&rooms={rooms}&square={min_surface}-max&"
    "locations={locations}&sort=time&order=desc"
)


def _wanted_room_counts(c: SearchCriteria) -> list[int]:
    """'T1'/'T2'… from the preferred + acceptable types → [1, 2]."""
    counts: set[int] = set()
    for t in c.property.types.preferred + c.property.types.acceptable:
        m = re.fullmatch(r"[TtFf](\d+)", t.strip())
        if m:
            counts.add(int(m.group(1)))
        elif t.strip().lower() == "studio":
            counts.add(1)
    return sorted(counts)


def _min_surface(c: SearchCriteria) -> int:
    """Smallest surface floor among the wanted types (URL filter = loosest bound)."""
    wanted = c.property.types.preferred + c.property.types.acceptable
    floors = [v for k, v in c.property.min_surface.items() if k in wanted]
    floors = floors or list(c.property.min_surface.values())
    return int(min(floors)) if floors else 0


def _lbc_locations(c: SearchCriteria) -> list[str]:
    """``zones.include`` → LBC ``City_Postcode`` tokens, one per commune.

    LBC's ``locations`` param takes comma-separated ``<City>_<postcode>``
    tokens (the same slug as its ``/cl/locations/cp_clichy_92110`` pages); a
    city token covers the whole commune, so rules sharing an INSEE code
    (Saint-Denis 93200 + 93210) collapse to the first one.
    """
    tokens: list[str] = []
    seen: set[str] = set()
    for z in c.zones.include:
        key = z.insee or f"{z.city}|{z.code}"
        if key in seen:
            continue
        seen.add(key)
        city = z.city.strip().replace(" ", "-")
        tokens.append(f"{city}_{z.code}" if city else z.code)
    return tokens


def _build_listing_url(criteria=None) -> str:
    c = criteria or get_criteria()
    rooms = _wanted_room_counts(c)
    rooms_param = f"{rooms[0]}-{rooms[-1]}" if rooms else "min-max"
    return LBC_LISTING_URL.format(
        max_price=int(c.property.max_rent_cc),
        rooms=rooms_param,
        min_surface=_min_surface(c) or "min",
        locations=quote(",".join(_lbc_locations(c)), safe=",_-"),
    )


def _type_from_rooms(rooms: int | None) -> str | None:
    if rooms is None:
        return None
    return f"T{rooms}" if 1 <= rooms <= 6 else None


_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


async def _extract_next_data(page: Page) -> dict[str, Any] | None:
    try:
        html = await page.content()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"page.content() failed: {exc}")
        return None
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        logger.debug(f"__NEXT_DATA__ JSON parse failed: {exc}")
        return None


def _walk_for_ads(obj: Any) -> list[dict[str, Any]]:
    """Find an `ads` list in the hydrated Next data (robust to minor schema shifts)."""
    found: list[dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if "ads" in node and isinstance(node["ads"], list):
                found.extend(a for a in node["ads"] if isinstance(a, dict) and a.get("list_id"))
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(obj)
    return found


def _attr(ad: dict[str, Any], key: str) -> str | None:
    for a in ad.get("attributes", []) or []:
        if a.get("key") == key:
            return a.get("value_label") or a.get("value")
    return None


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ".").split()[0])
    except (ValueError, TypeError):
        return None


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


def _parse_lbc_date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None


def prefilter_ad(
    ad: dict[str, Any],
    criteria: SearchCriteria,
    *,
    max_age_days: int,
    now: datetime,
) -> tuple[bool, str | None]:
    """Cheap reject from listing-page data BEFORE paying for a detail fetch.

    Conservative — only rejects on what's clearly visible in the ad; any missing
    field means "keep" (let the detail fetch + scoring decide). Returns
    ``(keep, reason)``.
    """
    # Recency — skip stale listings (the costly backlog after a downtime).
    if max_age_days > 0:
        pub = _parse_lbc_date(ad.get("first_publication_date") or ad.get("index_date"))
        if pub is not None and (now - pub).days > max_age_days:
            return False, f"publiée il y a >{max_age_days}j"

    # Rent
    price = ad.get("price")
    rent = _to_float(price[0] if isinstance(price, list) and price else price)
    if rent is not None and rent > criteria.property.max_rent_cc:
        return False, f"loyer {rent:.0f}>{criteria.property.max_rent_cc:.0f}"

    # Property type — drop room counts that aren't wanted (e.g. T3+).
    rooms = _to_int(_attr(ad, "rooms"))
    prop_type = _type_from_rooms(rooms)
    allowed = set(criteria.property.types.preferred + criteria.property.types.acceptable)
    if prop_type and prop_type not in allowed:
        return False, f"type {prop_type}"

    # Surface — only when the type's minimum is known.
    surface = _to_float(_attr(ad, "square"))
    min_surf = criteria.property.min_surface.get(prop_type) if prop_type else None
    if surface is not None and min_surf is not None and surface < min_surf:
        return False, f"surface {surface:.0f}<{min_surf:.0f}"

    # Zone — leboncoin ships zipcode + lat/lng, so the gate is reliable here.
    loc = ad.get("location") or {}
    postcode = loc.get("zipcode") or loc.get("postal_code")
    lat = _to_float(loc.get("lat"))
    lon = _to_float(loc.get("lng"))
    if postcode and not is_in_target_zone(criteria, postcode=postcode, lat=lat, lon=lon):
        return False, f"hors zone ({postcode})"

    return True, None


class LeBonCoinScraper(BaseScraper):
    source = "leboncoin"
    # DataDome — fetch via Scrapfly. JS render needed for __NEXT_DATA__ hydration.
    use_scrapfly = True
    scrapfly_render_js = True

    def _should_fetch_detail(self, entry: dict[str, Any]) -> bool:
        ad = entry.get("ad") or {}
        if not ad:
            return True
        keep, reason = prefilter_ad(
            ad,
            self.criteria,
            max_age_days=get_settings().max_listing_age_days,
            now=datetime.now(),
        )
        if not keep:
            logger.debug(f"[leboncoin] skip detail {entry.get('external_id')}: {reason}")
        return keep

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        url = _build_listing_url(self.criteria)
        logger.info(f"[leboncoin] fetching listing {url}")
        await page.goto(url, timeout=self.listing_timeout_ms, wait_until="domcontentloaded")
        # Wait for the hydrated state. LBC renders the full result list client-side.
        try:
            await page.wait_for_selector("script#__NEXT_DATA__", timeout=10_000)
        except Exception:  # noqa: BLE001
            logger.warning("[leboncoin] __NEXT_DATA__ not found within 10s")

        data = await _extract_next_data(page)
        if not data:
            logger.warning("[leboncoin] no __NEXT_DATA__ — DataDome may be blocking")
            return []

        ads = _walk_for_ads(data)
        entries: list[dict[str, str]] = []
        for ad in ads:
            list_id = ad.get("list_id")
            url_ad = ad.get("url") or (f"https://www.leboncoin.fr/ad/locations/{list_id}")
            if not list_id:
                continue
            entry: dict[str, Any] = {"external_id": str(list_id), "url": str(url_ad), "ad": ad}
            loc = ad.get("location") or {}
            postcode = loc.get("zipcode") or loc.get("postal_code")
            if postcode:
                # Lets the base zone pre-filter decide without a detail fetch.
                entry["postal_code"] = str(postcode)
            entries.append(entry)
        logger.debug(f"[leboncoin] parsed {len(entries)} ads from __NEXT_DATA__")
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, Any]) -> RawListing | None:
        # We already have most data from __NEXT_DATA__; only fetch detail for full description + photos.
        ad: dict[str, Any] = entry.get("ad") or {}
        url = entry["url"]

        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
            detail_data = await _extract_next_data(page)
            if detail_data:
                detail_ad = _find_ad_in_detail(detail_data) or {}
                if detail_ad:
                    ad = {**ad, **detail_ad}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[leboncoin] detail nav failed for {url}: {exc}")

        title = ad.get("subject") or ""
        description = ad.get("body")

        rooms = _to_int(_attr(ad, "rooms"))
        surface = _to_float(_attr(ad, "square"))
        rent = _to_float(
            ad.get("price", [None])[0] if isinstance(ad.get("price"), list) else ad.get("price")
        )
        charges = _to_float(_attr(ad, "charges_included"))
        furnished_label = (_attr(ad, "furnished") or "").lower()
        # Bug fix: "non meuble" contains "meuble" — substring check was setting
        # furnished=True for non-meublé listings. Detect "non" first.
        if not furnished_label:
            furnished = None
        else:
            nrm = normalise_text(furnished_label)
            if "non" in nrm:
                furnished = False
            elif "meuble" in nrm:
                furnished = True
            else:
                furnished = None
        floor = _to_int(_attr(ad, "floor_number"))
        elevator = _attr(ad, "elevator")
        has_elevator = (
            True
            if elevator and "oui" in normalise_text(elevator)
            else False
            if elevator and "non" in normalise_text(elevator)
            else None
        )
        property_type_label = _attr(ad, "real_estate_type") or ""
        property_type = _type_from_rooms(rooms) or (
            property_type_label.upper() if property_type_label else None
        )

        location = ad.get("location") or {}
        postcode = location.get("zipcode") or location.get("postal_code")
        lat = _to_float(location.get("lat"))
        lon = _to_float(location.get("lng"))
        address_bits = [location.get("city"), postcode]
        address = ", ".join(b for b in address_bits if b) or None

        photos = []
        images = ad.get("images") or {}
        if isinstance(images, dict):
            urls = images.get("urls_large") or images.get("urls") or []
            if isinstance(urls, list):
                photos = [u for u in urls if isinstance(u, str)]
        elif isinstance(images, list):
            photos = [u for u in images if isinstance(u, str)]

        raw = RawListing(
            source=self.source,
            external_id=str(ad.get("list_id") or entry["external_id"]),
            url=url,
            title=title,
            description=description,
            rent_cc=rent,
            charges=charges,
            surface_m2=surface,
            rooms=rooms,
            property_type=property_type,
            furnished=furnished,
            floor=floor,
            has_elevator=has_elevator,
            postal_code=str(postcode) if postcode else None,
            latitude=lat,
            longitude=lon,
            address=address,
            photo_urls=photos,
            raw={"ad": ad},
        )
        return raw


def _find_ad_in_detail(data: dict[str, Any]) -> dict[str, Any] | None:
    """Recursively look up the detail-page ad payload."""

    def _walk(node: Any) -> dict[str, Any] | None:
        if isinstance(node, dict):
            if node.get("list_id") and node.get("subject"):
                return node
            for v in node.values():
                found = _walk(v)
                if found:
                    return found
        elif isinstance(node, list):
            for v in node:
                found = _walk(v)
                if found:
                    return found
        return None

    return _walk(data)

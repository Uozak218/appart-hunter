"""Bien'ici scraper.

Bien'ici exposes a public JSON ``realEstateAds`` endpoint that's much friendlier
than scraping rendered HTML. Towns come from ``criteria.zones.include``: each
INSEE code is resolved to Bien'ici's internal zone id through its public
place-suggest API (``res.bienici.com/suggest.json``), and all zones go into a
single ``zoneIdsByTypes`` filter.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from playwright.async_api import Page

from src.config import SearchCriteria, ZoneRule
from src.schemas.listing import RawListing
from src.scrapers.base import BaseScraper
from src.scrapers.proxies import get_httpx_proxy
from src.utils.logging import logger

BIENICI_API = "https://www.bienici.com/realEstateAds.json"
BIENICI_SUGGEST_API = "https://res.bienici.com/suggest.json"

# Last-resort fallback when the suggest API is unreachable (INSEE → zone id,
# verified 2026-10 via suggest.json). The live lookup is the source of truth:
# a town added to the YAML works without touching this table.
KNOWN_ZONE_IDS: dict[str, str] = {
    "92044": "-86985",  # Levallois-Perret
    "92024": "-86999",  # Clichy
    "92004": "-91641",  # Asnières-sur-Seine
    "92009": "-91746",  # Bois-Colombes
    "92035": "-91775",  # La Garenne-Colombes
    "92025": "-91738",  # Colombes
    "93070": "-87002",  # Saint-Ouen-sur-Seine
    "93066": "-87922",  # Saint-Denis
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Referer": "https://www.bienici.com/recherche/location",
}

_POSTCODE_RE = re.compile(r"\b(\d{5})\b")

# Shared town-search helpers (also used by locservice / superimmo / foncia).

# Process-level cache: INSEE → zone id (resolved once per run).
_zone_id_cache: dict[str, str] = {}


def unique_towns(criteria: SearchCriteria) -> list[ZoneRule]:
    """``zones.include`` deduped by INSEE (falls back to postcode), order kept."""
    seen: set[str] = set()
    out: list[ZoneRule] = []
    for rule in criteria.zones.include:
        key = rule.insee or rule.code
        if key in seen:
            continue
        seen.add(key)
        out.append(rule)
    return out


def max_rooms(criteria: SearchCriteria) -> int | None:
    """Largest room count among wanted types (T1/T2 → 2). None if unparseable."""
    types = [*criteria.property.types.preferred, *criteria.property.types.acceptable]
    nums = [int(m.group(1)) for t in types if (m := re.search(r"(\d+)", t))]
    return max(nums) if nums else None


def passes_card_filters(criteria: SearchCriteria, entry: dict[str, Any]) -> bool:
    """Cheap listing-card check (rent, surface, rooms, furnished) before paying
    for a detail fetch. Unknown values pass — the post-detail filters decide."""
    p = criteria.property
    rent = entry.get("rent_cc")
    if rent is not None and rent > p.max_rent_cc:
        return False
    surface = entry.get("surface_m2")
    if surface is not None and p.min_surface and surface < min(p.min_surface.values()):
        return False
    rooms, cap = entry.get("rooms"), max_rooms(criteria)
    if rooms is not None and cap and rooms > cap:
        return False
    return not (p.furnished is False and entry.get("furnished") is True)


def build_filters(
    criteria: SearchCriteria, zone_ids: list[str], *, page: int = 1, size: int = 50
) -> dict[str, Any]:
    """The ``filters`` JSON payload for ``realEstateAds.json``."""
    c = criteria.property
    filters: dict[str, Any] = {
        "size": size,
        "from": (page - 1) * size,
        "filterType": "rent",
        "propertyType": ["flat"],
        "page": page,
        "sortBy": "publicationDate",
        "sortOrder": "desc",
        "onTheMarket": [True],
        "maxPrice": int(c.max_rent_cc),
        "minArea": int(min(c.min_surface.values())) if c.min_surface else 20,
        "zoneIdsByTypes": {"zoneIds": zone_ids},
    }
    rooms = max_rooms(criteria)
    if rooms:
        filters["maxRooms"] = rooms
    # Deliberately no ``isFurnished: false`` — it also drops ads whose
    # furnished flag is unknown (null); explicit furnished ads are dropped
    # client-side instead when ``property.furnished`` is False.
    return filters


def pick_zone_id(suggestions: list[dict[str, Any]], rule: ZoneRule) -> str | None:
    """Zone id of the suggest.json city entry matching the rule's INSEE/postcode."""
    for s in suggestions:
        if s.get("type") != "city":
            continue
        inseez = set(s.get("insee_codes") or []) | {s.get("insee_code")}
        if rule.insee and rule.insee in inseez:
            return (s.get("zoneIds") or [None])[0]
        if not rule.insee and rule.code in (s.get("postalCodes") or []):
            return (s.get("zoneIds") or [None])[0]
    return None


def ad_postcode(ad: dict[str, Any]) -> str | None:
    pc = ad.get("postalCode")
    if pc and re.fullmatch(r"\d{5}", str(pc)):
        return str(pc)
    m = _POSTCODE_RE.search(str(ad.get("city") or "") + " " + str(ad.get("title") or ""))
    return m.group(1) if m else None


class BienIciScraper(BaseScraper):
    source = "bienici"
    use_http_only = True

    async def _resolve_zone_ids(self, client: httpx.AsyncClient) -> list[str]:
        ids: list[str] = []
        for rule in unique_towns(self.criteria):
            key = rule.insee or rule.code
            zid = _zone_id_cache.get(key)
            if zid is None:
                for q in filter(None, (rule.city, rule.code)):
                    try:
                        r = await client.get(BIENICI_SUGGEST_API, params={"q": q})
                        r.raise_for_status()
                        zid = pick_zone_id(r.json(), rule)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"[bienici] suggest failed for {q!r}: {exc}")
                    if zid:
                        break
                zid = zid or KNOWN_ZONE_IDS.get(rule.insee)
                if not zid:
                    logger.warning(f"[bienici] no zone id for {rule.city} ({rule.code}); skipped")
                    continue
                _zone_id_cache[key] = zid
            if zid not in ids:
                ids.append(zid)
        return ids

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:  # noqa: ARG002
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS, proxy=get_httpx_proxy(), timeout=20.0
            ) as client:
                zone_ids = await self._resolve_zone_ids(client)
                if not zone_ids:
                    logger.warning("[bienici] no zone resolved from criteria.zones.include")
                    return []
                params = {
                    "filters": json.dumps(build_filters(self.criteria, zone_ids)),
                    "extensionType": "extendedIfNoResult",
                }
                r = await client.get(BIENICI_API, params=params)
                r.raise_for_status()
                data = r.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[bienici] API call failed: {exc}")
            return []

        unfurnished_only = self.criteria.property.furnished is False
        entries: list[dict[str, Any]] = []
        for ad in data.get("realEstateAds", []):
            aid = ad.get("id")
            if not aid:
                continue
            if unfurnished_only and ad.get("isFurnished") is True:
                continue
            url = ad.get("url") or f"https://www.bienici.com/annonce/location/{aid}"
            if url.startswith("/"):
                url = "https://www.bienici.com" + url
            entries.append(
                {"external_id": str(aid), "url": url, "postal_code": ad_postcode(ad), "ad": ad}
            )
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, Any]) -> RawListing | None:  # noqa: ARG002
        ad = entry.get("ad") or {}
        photos = [p.get("url") for p in (ad.get("photos") or []) if p.get("url")]
        position = (ad.get("blurInfo") or {}).get("position") or {}
        return RawListing(
            source=self.source,
            external_id=str(ad.get("id") or entry["external_id"]),
            url=str(entry["url"]),
            title=ad.get("title") or ad.get("adTitle") or "",
            description=ad.get("description"),
            rent_cc=ad.get("price"),
            charges=ad.get("charges"),
            surface_m2=ad.get("surfaceArea"),
            rooms=ad.get("roomsQuantity"),
            property_type=(f"T{ad.get('roomsQuantity')}" if ad.get("roomsQuantity") else None),
            furnished=ad.get("isFurnished"),
            floor=ad.get("floor"),
            has_elevator=ad.get("hasElevator"),
            postal_code=entry.get("postal_code") or ad_postcode(ad),
            latitude=position.get("lat"),
            longitude=position.get("lon"),
            address=ad.get("city"),
            photo_urls=photos,
            raw=ad,
        )

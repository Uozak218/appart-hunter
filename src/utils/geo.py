"""Geo helpers: Île-de-France postcode parsing + zone matching against search criteria."""

from __future__ import annotations

import re

from src.config import SearchCriteria, TouristicBBox, ZoneRule
from src.utils.hashing import normalise_text
from src.utils.metro import (
    haversine_meters,
    is_near_line14_station,
    is_near_well_connected_metro_20e,
    is_paris_intramuros,
)

# Île-de-France départements only (75, 77, 78, 91-95): a bare 5-digit match
# would also catch listing ids in URLs.
_POSTAL_RE = re.compile(r"\b((?:75|77|78|9[1-5])\d{3})\b")


def extract_postal_code(text: str | None) -> str | None:
    if not text:
        return None
    m = _POSTAL_RE.search(text)
    return m.group(1) if m else None


def arrondissement_from_postcode(postcode: str | None) -> int | None:
    """'75011' -> 11. Returns None if not a Paris intramuros postcode."""
    if not postcode or not postcode.startswith("75") or len(postcode) != 5:
        return None
    try:
        arr = int(postcode[2:])
        return arr if 1 <= arr <= 20 else None
    except ValueError:
        return None


def in_bbox(lat: float, lon: float, bbox: TouristicBBox) -> bool:
    return bbox.min_lat <= lat <= bbox.max_lat and bbox.min_lon <= lon <= bbox.max_lon


def zone_rule_for(criteria: SearchCriteria, postcode: str | None) -> ZoneRule | None:
    if not postcode:
        return None
    for rule in criteria.zones.include:
        if rule.code == postcode:
            return rule
    return None


def _in_sub_area(rule: ZoneRule, lat: float | None, lon: float | None, text: str | None) -> bool:
    """For a postcode restricted to one sub-area (Saint-Denis → Pleyel/Plaine).

    Coords win when present. Without coords we fall back to keywords in the
    listing text; with neither, the listing is rejected — the restriction exists
    precisely to keep the rest of the commune out.
    """
    if rule.include_bbox and lat is not None and lon is not None:
        return in_bbox(lat, lon, rule.include_bbox)
    if text and rule.include_keywords:
        norm = normalise_text(text)
        return any(normalise_text(kw) in norm for kw in rule.include_keywords)
    return False


def is_in_target_zone(
    criteria: SearchCriteria,
    *,
    postcode: str | None,
    lat: float | None = None,
    lon: float | None = None,
    text: str | None = None,
) -> bool:
    """True iff this listing passes the zone gate.

    Two paths accept a listing:

    1. The postcode is explicitly listed in ``zones.include`` (with the
       touristic-bbox exclusion honored when geocoords are available).
    2. ``zones.line14_proximity.enabled`` is true AND the listing is in any
       Paris intramuros arrondissement AND within the configured walk-time of
       a line-14 station.

    Line-14 proximity is an expansion, not a replacement — an annotated zone
    rule still wins even if the listing is also next to line 14.
    """
    rule = zone_rule_for(criteria, postcode)
    if rule is not None:
        if (rule.include_bbox or rule.include_keywords) and not _in_sub_area(rule, lat, lon, text):
            return False
        in_tourist_bbox = (
            rule.exclude_touristic
            and rule.touristic_bbox
            and lat is not None
            and lon is not None
            and in_bbox(lat, lon, rule.touristic_bbox)
        )
        if in_tourist_bbox:
            return False
        if rule.require_well_connected_metro:
            # The metro-proximity check needs coords. Some sources (Foncia) never
            # geocode, so lat/lon are None — don't silently drop those good
            # matches (a 1253€ vide T2 in the 20e was lost this way). Accept when
            # we can't verify; only gate on proximity when coords ARE present.
            if lat is None or lon is None:
                return True
            return is_near_well_connected_metro_20e(lat, lon)
        return True

    # Fallback: line-14 proximity expansion
    line14 = criteria.zones.line14_proximity
    return (
        line14.enabled
        and is_paris_intramuros(postcode)
        and is_near_line14_station(lat, lon, max_walk_minutes=line14.max_walk_minutes)
    )


def commute_distance_km(
    criteria: SearchCriteria,
    *,
    postcode: str | None,
    lat: float | None,
    lon: float | None,
) -> tuple[float, bool] | None:
    """Straight-line km from the listing to ``criteria.commute_to``.

    Returns ``(km, exact)``: exact when the listing has coordinates, otherwise
    measured from the zone centre declared in the YAML. ``None`` when neither
    is available or no commute target is configured.
    """
    target = criteria.commute_to
    if target is None:
        return None
    if lat is not None and lon is not None:
        return haversine_meters(lat, lon, target.lat, target.lon) / 1000, True
    rule = zone_rule_for(criteria, postcode)
    if rule is not None and rule.lat is not None and rule.lon is not None:
        return haversine_meters(rule.lat, rule.lon, target.lat, target.lon) / 1000, False
    return None


def estimate_commute_minutes(km: float) -> tuple[int, str]:
    """Rough door-to-door time from straight-line distance alone.

    Walking under 1.5 km (~12 min/km). Beyond, public transport in the inner
    suburbs: walk to the station + wait + ride grows sub-linearly with distance
    (4 + 11·√km — ~20 min at 2-3 km, ~30 at 5, ~35 at 8, ~50 at 18). No
    timetable involved: a listing next to a direct train beats it, one served
    only by bus is slower.
    """
    if km <= 1.5:
        return max(1, round(km * 12)), "à pied"
    return round(4 + 11 * km**0.5), "en transports"

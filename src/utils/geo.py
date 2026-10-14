"""Geo helpers: Paris postcode parsing + zone matching against search criteria."""

from __future__ import annotations

import re

from src.config import SearchCriteria, TouristicBBox, ZoneRule
from src.utils.metro import (
    is_near_line14_station,
    is_near_well_connected_metro_20e,
    is_paris_intramuros,
)

_POSTAL_RE = re.compile(r"\b(75\d{3})\b")


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


def is_in_target_zone(
    criteria: SearchCriteria,
    *,
    postcode: str | None,
    lat: float | None = None,
    lon: float | None = None,
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

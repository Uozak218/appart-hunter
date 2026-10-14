"""Cross-site fuzzy deduplication helpers."""

from __future__ import annotations

from rapidfuzz import fuzz

from src.models.listing import Listing
from src.schemas.listing import RawListing
from src.utils.hashing import normalise_text

TITLE_SIMILARITY_THRESHOLD = 80  # 0-100
SURFACE_TOLERANCE_M2 = 2.0
PRICE_TOLERANCE_EUR = 80.0


def is_cross_site_duplicate(raw: RawListing, candidate: Listing) -> bool:
    """Return True if `candidate` appears to be the same listing as `raw` on a different site."""
    if candidate.source == raw.source:
        return False
    if candidate.postal_code and raw.postal_code and candidate.postal_code != raw.postal_code:
        return False
    if (
        raw.surface_m2
        and candidate.surface_m2
        and abs(raw.surface_m2 - candidate.surface_m2) > SURFACE_TOLERANCE_M2
    ):
        return False
    if (
        raw.rent_cc
        and candidate.rent_cc
        and abs(raw.rent_cc - candidate.rent_cc) > PRICE_TOLERANCE_EUR
    ):
        return False

    a = normalise_text(raw.title)
    b = normalise_text(candidate.title)
    if not a or not b:
        return False
    similarity = fuzz.token_set_ratio(a, b)
    return similarity >= TITLE_SIMILARITY_THRESHOLD


def find_duplicate(raw: RawListing, candidates: list[Listing]) -> Listing | None:
    for c in candidates:
        if is_cross_site_duplicate(raw, c):
            return c
    return None

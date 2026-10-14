"""Deterministic Python pre-filter.

Runs the same hard-criteria checks as the Haiku system prompt but in pure
Python. When every relevant field is present and the answer is obvious, we
return a verdict and skip the Haiku call entirely (saving ~50% of Haiku
tokens in steady state).

Returns:
  - (True, reasons)  — passes, go straight to Sonnet scoring
  - (False, reasons) — fails, store status=filtered_out, no Claude call
  - (None, reasons)  — ambiguous (missing data), delegate to Haiku

Ambiguity rules (delegate to Haiku):
  - property_type unknown AND rooms unknown
  - furnished unknown (could be "meublé partiel" etc.)
  - floor unknown when elevator_required_above kicks in
"""

from __future__ import annotations

import re

from src.config import SearchCriteria
from src.models.listing import Listing
from src.utils.geo import is_in_target_zone
from src.utils.hashing import normalise_text

_NEGATION_BEFORE = re.compile(r"\b(sans|pas de|pas d|ni|aucune|aucun|non|plus de)\s+(\w+\s+){0,2}$")
_NEGATION_AFTER = re.compile(r"^\s+(\w+\s+){0,2}(remplacee|remplace|transformee|supprimee)\b")


def _mentions(blob: str, kw_norm: str) -> bool:
    """True if ``kw_norm`` appears in ``blob`` at least once without a negation.

    "sans baignoire", "pas de colocation", "baignoire remplacée par une douche"
    are not mentions; "salle de bains avec baignoire" is.
    """
    for m in re.finditer(rf"\b{re.escape(kw_norm)}\b", blob):
        before = blob[max(0, m.start() - 40) : m.start()]
        after = blob[m.end() : m.end() + 40]
        if _NEGATION_BEFORE.search(before) or _NEGATION_AFTER.search(after):
            continue
        return True
    return False


def hard_prefilter(listing: Listing, criteria: SearchCriteria) -> tuple[bool | None, list[str]]:
    reasons: list[str] = []

    # Zone gate — defers to utils.geo.is_in_target_zone so line-14 proximity
    # expansion is honored identically across the pipeline.
    if not listing.postal_code:
        reasons.append("postcode missing")
        return None, reasons
    if not is_in_target_zone(
        criteria,
        postcode=listing.postal_code,
        lat=listing.latitude,
        lon=listing.longitude,
        text=" ".join(
            filter(None, [listing.title, listing.description, getattr(listing, "address", None)])
        ),
    ):
        return False, [f"postcode {listing.postal_code} outside target zones (no line-14 match)"]

    # Rent
    if listing.rent_cc is None:
        reasons.append("rent missing")
        return None, reasons
    if listing.rent_cc > criteria.property.max_rent_cc:
        return False, [f"rent {listing.rent_cc}€ > max {criteria.property.max_rent_cc}€"]

    # Property type + surface
    prop_type = listing.property_type
    if prop_type is None and listing.rooms is not None and 1 <= listing.rooms <= 6:
        prop_type = f"T{listing.rooms}"
    allowed_types = set(criteria.property.types.preferred + criteria.property.types.acceptable)
    if prop_type is None:
        reasons.append("property_type + rooms missing")
        return None, reasons
    if prop_type not in allowed_types:
        return False, [f"type {prop_type} not in {sorted(allowed_types)}"]
    min_surface = criteria.property.min_surface.get(prop_type)
    if listing.surface_m2 is None:
        reasons.append("surface missing")
        return None, reasons
    if min_surface is not None and listing.surface_m2 < min_surface:
        return False, [
            f"surface {listing.surface_m2:.0f}m² < min {min_surface:.0f}m² for {prop_type}"
        ]

    # Furnished — only enforce when criteria explicitly demand non-meublé.
    # When ``criteria.property.furnished is None``, we accept both: Claude
    # will surface a 🛋️ badge in the alert so the user can triage at read
    # time without missing a cachet pépite that happens to be meublée.
    if criteria.property.furnished is False:
        if listing.furnished is True:
            return False, ["furnished listing, we want vide"]
        if listing.furnished is None:
            desc = normalise_text(" ".join(filter(None, [listing.title, listing.description])))
            if re.search(r"\bmeuble\b", desc) and not re.search(r"\bnon meuble\b", desc):
                return False, ["description mentions meublé"]

    # Floor rules
    rules = criteria.property.floor
    if listing.floor == 0 and rules.no_ground_floor:
        return False, ["ground floor (RDC) excluded"]
    if (
        listing.floor is not None
        and listing.floor > rules.elevator_required_above
        and listing.has_elevator is False
    ):
        return False, [f"floor {listing.floor} > {rules.elevator_required_above} without elevator"]

    # Excludes keywords (colocation, sous-location, courte-duree, baignoire...)
    # Word-boundary match to avoid false positives (e.g. "meuble" in "immeuble"),
    # and negated mentions ("sans baignoire", "non meublé") don't count.
    blob = normalise_text(" ".join(filter(None, [listing.title, listing.description])))
    for kw in criteria.excludes:
        kw_norm = normalise_text(kw)
        if kw_norm and _mentions(blob, kw_norm):
            return False, [f"excluded keyword: {kw}"]

    # Got this far — it's a pass. If any field was borderline, still pass.
    return True, reasons or ["all hard criteria met"]

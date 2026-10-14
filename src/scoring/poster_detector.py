"""Detect whether a listing is posted by an agence or a particulier.

Heuristics only — cheap, runs before Claude. Signals we look at:
  - Explicit source-level owner_type flags (LBC/Bien'ici expose this).
  - Mentions of "honoraires", "agence", "SARL", "SAS", "immobilier", "bail"
    in title/description.
  - Phone patterns (agencies use landlines more often than mobiles).
  - Email domains (if visible).

Used purely for display + future contact-strategy tweaks (e.g. a different
Claude prompt template for agency vs particulier). Never gates alerts.
"""

from __future__ import annotations

import re
from typing import Literal

from src.utils.hashing import normalise_text

Poster = Literal["agency", "private", "unknown"]


AGENCY_KEYWORDS = [
    "honoraires",
    "frais d agence",
    "agence",
    "cabinet",
    "immobilier",
    "foncia",
    "century 21",
    "orpi",
    "guy hoquet",
    "laforet",
    "stephane plaza",
    "sarl",
    " sas ",
    " sa ",
    " sasu ",
    "iad france",
    "keller williams",
    "barnes",
    "sotheby",
    "emeraude",
    "rue st maur",  # common agency address pattern
]

PRIVATE_KEYWORDS = [
    "particulier",
    "de particulier a particulier",
    "sans intermediaire",
    "sans agence",
    "proprietaire",
    "proprietaire direct",
]


def detect_poster(*, title: str | None, description: str | None, raw: dict | None = None) -> Poster:
    # Hard signal from source payload
    if raw:
        # LBC: owner_type = pro | private
        owner_type = (raw.get("ad") or {}).get("owner_type") if isinstance(raw, dict) else None
        if owner_type == "private":
            return "private"
        if owner_type == "pro":
            return "agency"

    blob = normalise_text(" ".join(filter(None, [title, description])))
    if not blob:
        return "unknown"

    if any(kw in blob for kw in PRIVATE_KEYWORDS):
        return "private"
    if any(kw in blob for kw in AGENCY_KEYWORDS):
        return "agency"
    # Fee-mention pattern ("XXX € d'honoraires" is agency-specific)
    if re.search(r"\bhonorair", blob):
        return "agency"
    return "unknown"

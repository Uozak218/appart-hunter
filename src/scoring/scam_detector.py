"""Anti-scam detector.

Runs cheap heuristics on every listing BEFORE we burn Claude tokens. Common
Paris scam patterns:
  - Way-below-market price (e.g. T3 at 800€ in 75011).
  - Mention of Western Union / wire transfer / "à l'étranger" / "agent of God".
  - Email or phone in description with foreign country code.
  - Description mostly in English on a French listing.
  - "Premier mois offert", "déposit en bitcoin", "envoi des clés par la poste".

Listings flagged as `is_scam=True` are stored, never alerted, and surfaced in
a dedicated Dashboard panel for inspection. Flag goes into ListingStatus and
into the Sonnet system prompt so the gem score also takes it into account.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.config import MarketPrices
from src.schemas.listing import RawListing
from src.utils.geo import arrondissement_from_postcode
from src.utils.hashing import normalise_text

# Fallback used when no MarketPrices is supplied (keeps tests + legacy callers
# working without a criteria object). Real callers pass the per-arrondissement
# floor table from the YAML.
PARIS_RENT_FLOOR_EUR_PER_M2 = 28.0


SCAM_KEYWORDS = [
    "western union",
    "moneygram",
    "transfer urgent",
    "envoi des cles par la poste",
    "envoi cles par la poste",
    "envoyer les cles",
    "deposit en bitcoin",
    "depot en bitcoin",
    "1er mois offert",
    "premier mois offert",
    "loyer offert",
    "actuellement a l etranger",
    "actuellement en mission",
    "agent of god",
    "missionnaire",
    "diplomate a l etranger",
    "envoyer une copie de votre piece d identite",
    "envoyer cni",
    "transfer 1 mois caution",
    "caution par virement",
]

FOREIGN_PHONE_RE = re.compile(r"\+(?!33)\d{1,3}[\d\s\.\-]{6,}")
EMAIL_BLAST_RE = re.compile(r"\b\S+@(gmail|yahoo|hotmail|outlook)\.\S+\s*[.,;]\s*\S+@", re.I)


@dataclass
class ScamReport:
    is_scam: bool
    score: int  # 0 (clean) -> 100 (definite scam)
    reasons: list[str]


def detect_scam(raw: RawListing, market_prices: MarketPrices | None = None) -> ScamReport:
    reasons: list[str] = []
    score = 0
    blob = normalise_text(" ".join(filter(None, [raw.title, raw.description])))

    # 1. Far-below-market price (per-quartier > per-arr calibration if available)
    arr = arrondissement_from_postcode(raw.postal_code)
    listing_text = " ".join(filter(None, [raw.title, raw.description, raw.address]))
    if market_prices is not None:
        floor = market_prices.floor_for(arr, text=listing_text, postcode=raw.postal_code)
    else:
        floor = PARIS_RENT_FLOOR_EUR_PER_M2

    if raw.rent_cc and raw.surface_m2 and raw.surface_m2 >= 25:
        per_m2 = raw.rent_cc / raw.surface_m2
        if per_m2 < floor * 0.6:  # 40% under local floor
            reasons.append(f"prix anormalement bas ({per_m2:.1f}€/m², plancher {floor:.0f}€/m²)")
            score += 50
        elif per_m2 < floor * 0.8:
            reasons.append(f"prix sous le marché ({per_m2:.1f}€/m², plancher {floor:.0f}€/m²)")
            score += 20

    # 2. Scam keywords
    hits = [kw for kw in SCAM_KEYWORDS if kw in blob]
    if hits:
        reasons.append(f"mots-clés suspects: {', '.join(hits[:3])}")
        score += 30 * len(hits)

    # 3. Foreign phone (not +33)
    if raw.description and FOREIGN_PHONE_RE.search(raw.description):
        reasons.append("téléphone étranger dans la description")
        score += 25

    # 4. Multiple webmail addresses (mass-paste pattern)
    if raw.description and EMAIL_BLAST_RE.search(raw.description):
        reasons.append("plusieurs adresses webmail dans la description")
        score += 20

    # 5. Almost no photos for a posh-described place
    if raw.rent_cc and raw.rent_cc < 1500 and len(raw.photo_urls) <= 1:
        reasons.append("très peu de photos pour un loyer attractif")
        score += 15

    score = min(100, score)
    return ScamReport(is_scam=score >= 50, score=score, reasons=reasons)

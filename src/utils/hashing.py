"""Hashing helpers used for cross-site deduplication and external-id normalisation."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")


_LIGATURES = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "ß": "ss"})


def normalise_text(text: str) -> str:
    """Lowercase, strip accents + ligatures, collapse whitespace, drop punctuation."""
    if not text:
        return ""
    expanded = text.translate(_LIGATURES)
    nfkd = unicodedata.normalize("NFKD", expanded)
    without_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    lowered = without_accents.lower()
    alnum = _NON_ALNUM.sub(" ", lowered)
    return _WHITESPACE.sub(" ", alnum).strip()


def title_fingerprint(title: str, n_words: int = 3) -> str:
    """Return the first N normalised words of a title — stable across sites."""
    words = normalise_text(title).split()
    return " ".join(words[:n_words])


def dedup_key(
    *,
    title: str,
    surface: float | int | None,
    rent_cc: float | int | None,
    postal_code: str | None,
) -> str:
    """Fuzzy cross-site dedup key.

    Combines (3-word normalised title, rounded surface, rounded price bucket, postcode).
    Same-listing across sites will usually land in the same bucket; we still
    run rapidfuzz on titles/addresses for final matching (see storage.dedup).
    """
    t = title_fingerprint(title)
    s = int(round(float(surface))) if surface else 0
    # bucket the rent by 50€ steps to absorb tiny per-site variations
    p = int(round(float(rent_cc) / 50.0)) if rent_cc else 0
    z = (postal_code or "").strip()
    payload = f"{t}|{s}|{p}|{z}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def external_id_hash(source: str, external_id: str) -> str:
    """Stable per-site unique key."""
    return hashlib.sha1(f"{source}:{external_id}".encode()).hexdigest()

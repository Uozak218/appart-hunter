"""Pydantic schemas for listings passed between scrapers / scoring / notifier."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class RawListing(BaseModel):
    """Normalised shape produced by every scraper."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    source: str
    external_id: str
    url: HttpUrl | str
    title: str
    description: str | None = None
    rent_cc: float | None = None  # total rent incl. charges, EUR
    charges: float | None = None
    surface_m2: float | None = None
    rooms: int | None = None
    property_type: str | None = None  # inferred T2/T3/...
    furnished: bool | None = None
    floor: int | None = None
    has_elevator: bool | None = None
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    address: str | None = None
    photo_urls: list[str] = Field(default_factory=list)
    raw: dict[str, Any] | None = None
    seen_at: datetime | None = None
    # "agency" | "private" | "unknown" (best-effort heuristic; see poster_detector).
    poster_type: str | None = None


class ScoredListing(BaseModel):
    """Listing + scoring result — what the notifier consumes."""

    listing_id: int
    raw: RawListing
    score: int
    highlights: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    is_urgent: bool = False
    price_drop_pct: float | None = None
    # Verdict Claude sur le statut meublé. ``None`` = score legacy avant le
    # feature flag, on n'affiche pas de badge. ``unclear`` = on affiche ❓.
    furnished_status: str | None = None

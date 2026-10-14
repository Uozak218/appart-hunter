"""Listing + photos ORM models."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.storage.database import Base


class ListingStatus(enum.StrEnum):
    new = "new"
    scored = "scored"
    notified = "notified"
    filtered_out = "filtered_out"
    gone = "gone"
    scam = "scam"


class UserInterest(enum.StrEnum):
    none = "none"
    interested = "interested"
    not_interested = "not_interested"
    contacted = "contacted"


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Source tracking
    source: Mapped[str] = mapped_column(String(32), index=True)  # leboncoin, seloger, ...
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    url: Mapped[str] = mapped_column(Text)

    # Normalised fields
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rent_cc: Mapped[float | None] = mapped_column(Float, nullable=True)  # charges comprises
    charges: Mapped[float | None] = mapped_column(Float, nullable=True)
    surface_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    rooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    property_type: Mapped[str | None] = mapped_column(String(8), nullable=True)  # T2/T3/...
    furnished: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    floor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_elevator: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    postal_code: Mapped[str | None] = mapped_column(String(8), index=True, nullable=True)
    arrondissement: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Raw JSON scraped payload for future re-parsing
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Dedup / lifecycle
    external_hash: Mapped[str] = mapped_column(String(64), index=True)
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[ListingStatus] = mapped_column(
        Enum(ListingStatus, name="listing_status"),
        default=ListingStatus.new,
        index=True,
    )
    scam_score: Mapped[int] = mapped_column(Integer, default=0)
    scam_reasons: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    user_interest: Mapped[UserInterest] = mapped_column(
        Enum(UserInterest, name="user_interest"),
        default=UserInterest.none,
        index=True,
    )

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Set when an auto-application email is actually sent (live mode). Drives
    # both dedup (one application per listing) and the rolling-24h daily cap.
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    photos: Mapped[list[ListingPhoto]] = relationship(
        "ListingPhoto", back_populates="listing", cascade="all, delete-orphan"
    )
    price_history = relationship(
        "PriceHistory", back_populates="listing", cascade="all, delete-orphan"
    )
    scores = relationship("Score", back_populates="listing", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_listings_source_extid"),
        Index("ix_listings_dedup_key_status", "dedup_key", "status"),
    )


class ListingPhoto(Base):
    __tablename__ = "listing_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    listing: Mapped[Listing] = relationship("Listing", back_populates="photos")

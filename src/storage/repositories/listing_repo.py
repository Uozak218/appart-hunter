"""Listing repository — upsert/read helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.listing import Listing, ListingPhoto, ListingStatus
from src.models.price_history import PriceHistory
from src.schemas.listing import RawListing
from src.utils.geo import arrondissement_from_postcode
from src.utils.hashing import dedup_key, external_id_hash


def _content_hash(raw: RawListing) -> str:
    payload = {
        "title": raw.title,
        "description": raw.description or "",
        "rent_cc": raw.rent_cc,
        "surface_m2": raw.surface_m2,
        "photos": sorted(raw.photo_urls),
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class ListingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_source_extid(self, source: str, external_id: str) -> Listing | None:
        result = await self.session.execute(
            select(Listing).where(Listing.source == source, Listing.external_id == external_id)
        )
        return result.scalar_one_or_none()

    async def recent_dedup_keys(self, keys: list[str]) -> dict[str, Listing]:
        if not keys:
            return {}
        result = await self.session.execute(select(Listing).where(Listing.dedup_key.in_(keys)))
        return {row.dedup_key: row for row in result.scalars().all()}

    async def upsert(self, raw: RawListing) -> tuple[Listing, bool, bool]:
        """Insert or update. Returns (listing, created, price_changed)."""
        existing = await self.get_by_source_extid(raw.source, raw.external_id)
        content = _content_hash(raw)
        dedup = dedup_key(
            title=raw.title,
            surface=raw.surface_m2,
            rent_cc=raw.rent_cc,
            postal_code=raw.postal_code,
        )

        created = False
        price_changed = False
        now = datetime.now(UTC)

        if existing is None:
            listing = Listing(
                source=raw.source,
                external_id=raw.external_id,
                url=str(raw.url),
                title=raw.title,
                description=raw.description,
                rent_cc=raw.rent_cc,
                charges=raw.charges,
                surface_m2=raw.surface_m2,
                rooms=raw.rooms,
                property_type=raw.property_type,
                furnished=raw.furnished,
                floor=raw.floor,
                has_elevator=raw.has_elevator,
                postal_code=raw.postal_code,
                arrondissement=arrondissement_from_postcode(raw.postal_code),
                latitude=raw.latitude,
                longitude=raw.longitude,
                address=raw.address,
                raw=raw.raw,
                external_hash=external_id_hash(raw.source, raw.external_id),
                dedup_key=dedup,
                content_hash=content,
                status=ListingStatus.new,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(listing)
            await self.session.flush()
            for i, url in enumerate(raw.photo_urls):
                self.session.add(ListingPhoto(listing_id=listing.id, url=url, position=i))
            if raw.rent_cc is not None:
                self.session.add(PriceHistory(listing_id=listing.id, rent_cc=raw.rent_cc))
            created = True
            return listing, created, False

        # Existing: check for price change + content drift
        if (
            raw.rent_cc is not None
            and existing.rent_cc is not None
            and abs(raw.rent_cc - existing.rent_cc) >= 1.0
        ):
            self.session.add(PriceHistory(listing_id=existing.id, rent_cc=raw.rent_cc))
            existing.rent_cc = raw.rent_cc
            price_changed = True
        if existing.content_hash != content:
            existing.title = raw.title
            existing.description = raw.description
            existing.content_hash = content
            # Re-sync photos if they changed
            if sorted(raw.photo_urls) != sorted(p.url for p in existing.photos):
                for p in list(existing.photos):
                    await self.session.delete(p)
                await self.session.flush()
                for i, url in enumerate(raw.photo_urls):
                    self.session.add(ListingPhoto(listing_id=existing.id, url=url, position=i))
        existing.last_seen_at = now
        return existing, created, price_changed

    async def mark_status(self, listing: Listing, status: ListingStatus) -> None:
        listing.status = status
        await self.session.flush()

    async def count_by_status(self) -> dict[str, int]:
        result = await self.session.execute(
            select(Listing.status, func.count(Listing.id)).group_by(Listing.status)
        )
        return {str(s.value if s else "unknown"): int(c) for s, c in result.all()}

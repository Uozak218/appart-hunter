"""Health + readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db
from src.models.scraper_health import ScraperHealth

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready(session: AsyncSession = Depends(get_db)) -> dict:
    result = await session.execute(select(ScraperHealth))
    scrapers = [
        {
            "source": row.source,
            "last_success": row.last_success_at.isoformat() if row.last_success_at else None,
            "consecutive_failures": row.consecutive_failures,
            "total_runs": row.total_runs,
            "last_count": row.last_listings_count,
        }
        for row in result.scalars().all()
    ]
    return {"status": "ok", "scrapers": scrapers}

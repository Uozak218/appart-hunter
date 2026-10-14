"""Admin endpoints: stats, budget, manual triggers (authenticated)."""

from __future__ import annotations

import csv
import io
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import cast, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Date

from src.api.dependencies import get_db, require_admin
from src.config import get_criteria
from src.models.listing import Listing, ListingStatus, UserInterest
from src.models.score import ApiUsage, Score, ScoreStage
from src.notifier.queue import pause_until, paused_until, resume
from src.scheduler.jobs import run_scraper
from src.scoring.pipeline import ScoringPipeline
from src.scrapers import SCRAPER_REGISTRY
from src.storage.repositories.listing_repo import ListingRepository

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/stats")
async def stats(session: AsyncSession = Depends(get_db)) -> dict:
    listing_repo = ListingRepository(session)
    pipeline = ScoringPipeline(session, get_criteria())
    spent, cap, pct = await pipeline.budget.budget_state()
    return {
        "listings": await listing_repo.count_by_status(),
        "budget": {
            "spent_eur": round(spent, 4),
            "cap_eur": round(cap, 2),
            "pct": round(pct, 2),
            "allow_scoring": spent < cap,
        },
    }


@router.post("/scrape/{source}")
async def trigger_scrape(source: str, background: BackgroundTasks) -> dict:
    if source not in SCRAPER_REGISTRY:
        raise HTTPException(404, f"unknown scraper: {source}")
    background.add_task(run_scraper, source)
    return {"status": "scheduled", "source": source}


@router.get("/favorites.csv")
async def export_favorites(
    interest: str = "interested,contacted",
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Export the listings you marked as interested / contacted as a CSV.

    Filter via ``?interest=interested`` or ``?interest=interested,contacted``
    (default).
    """
    wanted = set()
    for token in interest.split(","):
        token = token.strip()
        if token in {ui.value for ui in UserInterest}:
            wanted.add(token)
    if not wanted:
        raise HTTPException(
            400, "interest must be one or more of: " + ",".join(ui.value for ui in UserInterest)
        )

    result = await session.execute(
        select(Listing)
        .where(Listing.user_interest.in_([UserInterest(v) for v in wanted]))
        .order_by(desc(Listing.last_seen_at))
    )
    rows = result.scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "id",
            "interest",
            "source",
            "external_id",
            "url",
            "title",
            "rent_cc",
            "surface_m2",
            "rooms",
            "type",
            "postal_code",
            "floor",
            "elevator",
            "address",
            "first_seen_at",
            "last_seen_at",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.id,
                r.user_interest.value if r.user_interest else "",
                r.source,
                r.external_id,
                r.url,
                r.title,
                r.rent_cc,
                r.surface_m2,
                r.rooms,
                r.property_type or "",
                r.postal_code or "",
                r.floor if r.floor is not None else "",
                "yes" if r.has_elevator else ("no" if r.has_elevator is False else ""),
                r.address or "",
                r.first_seen_at.isoformat() if r.first_seen_at else "",
                r.last_seen_at.isoformat() if r.last_seen_at else "",
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="pfh-favorites.csv"'},
    )


def _md_interest_emoji(interest: UserInterest | None) -> str:
    return {
        UserInterest.interested: "❤️",
        UserInterest.contacted: "📞",
        UserInterest.not_interested: "🚫",
    }.get(interest, "—")


@router.get("/favorites.md")
async def export_favorites_markdown(
    interest: str = "interested,contacted",
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Markdown version of the favorites export — easier to share in a DM."""
    wanted = set()
    for token in interest.split(","):
        token = token.strip()
        if token in {ui.value for ui in UserInterest}:
            wanted.add(token)
    if not wanted:
        raise HTTPException(
            400, "interest must be one or more of: " + ",".join(ui.value for ui in UserInterest)
        )

    result = await session.execute(
        select(Listing)
        .where(Listing.user_interest.in_([UserInterest(v) for v in wanted]))
        .order_by(desc(Listing.last_seen_at))
    )
    rows = result.scalars().all()

    lines: list[str] = ["# Paris Flat Hunter — Favoris", ""]
    if not rows:
        lines.append("_Rien encore._")
    else:
        lines.append(f"_{len(rows)} annonces, triées par dernière observation._")
        lines.append("")
        for r in rows:
            arr = f"75{r.arrondissement:02d}" if r.arrondissement else "—"
            price = f"{int(r.rent_cc)} € CC" if r.rent_cc else "—"
            surface = f"{int(r.surface_m2)} m²" if r.surface_m2 else "—"
            floor = f"{r.floor}e étage" if r.floor is not None else ""
            lift = "asc. ✅" if r.has_elevator else ("asc. ❌" if r.has_elevator is False else "")
            meta = " · ".join(filter(None, [arr, price, surface, r.property_type, floor, lift]))
            emoji = _md_interest_emoji(r.user_interest)
            title = (r.title or "").replace("|", "-")
            lines.append(f"## {emoji} [{title}]({r.url})")
            lines.append(f"*{meta}*")
            if r.address:
                lines.append(f"📍 {r.address}")
            if r.description:
                snippet = r.description.strip().replace("\n", " ")[:300]
                if snippet:
                    lines.append("")
                    lines.append(f"> {snippet}…")
            lines.append("")

    body = "\n".join(lines)
    return StreamingResponse(
        iter([body]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="pfh-favorites.md"'},
    )


@router.post("/pause")
async def pause_notifier(hours: float = 24.0) -> dict:
    """Mute alerts for N hours. Scraping + scoring continue normally.

    Useful during holidays or visits — you still get the weekly digest and
    full DB history when you resume. Max 168h (7 days).
    """
    hours = max(0.1, min(168.0, float(hours)))
    until = time.time() + hours * 3600
    pause_until(until)
    return {
        "paused": True,
        "until": datetime.fromtimestamp(until, tz=UTC).isoformat(),
        "hours": hours,
    }


@router.post("/resume")
async def resume_notifier() -> dict:
    resume()
    return {"paused": False}


@router.get("/pause")
async def pause_state() -> dict:
    until_ts = paused_until()
    return {
        "paused": time.time() < until_ts,
        "until": (datetime.fromtimestamp(until_ts, tz=UTC).isoformat() if until_ts else None),
    }


@router.get("/budget-history")
async def budget_history(
    days: int = 30,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Daily Claude spend (EUR) over the last ``days`` days, plus per-model split."""
    days = max(1, min(180, int(days)))
    since = datetime.now(UTC) - timedelta(days=days)

    daily = await session.execute(
        select(
            cast(ApiUsage.created_at, Date).label("day"),
            func.coalesce(func.sum(ApiUsage.cost_eur), 0.0).label("cost"),
        )
        .where(ApiUsage.created_at >= since)
        .group_by(cast(ApiUsage.created_at, Date))
        .order_by(cast(ApiUsage.created_at, Date))
    )
    by_model = await session.execute(
        select(
            ApiUsage.model,
            func.coalesce(func.sum(ApiUsage.cost_eur), 0.0),
        )
        .where(ApiUsage.created_at >= since)
        .group_by(ApiUsage.model)
    )

    return {
        "days": days,
        "daily": [{"day": str(d), "cost_eur": round(float(c), 4)} for d, c in daily.all()],
        "by_model": {str(m): round(float(c), 4) for m, c in by_model.all()},
    }


@router.get("/efficiency")
async def efficiency(
    days: int = 7,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Efficiency KPIs: cost/gem, prefilter hit-rate, cache hit-rate, etc."""
    days = max(1, min(90, int(days)))
    since = datetime.now(UTC) - timedelta(days=days)

    listings_total = (
        await session.execute(select(func.count(Listing.id)).where(Listing.first_seen_at >= since))
    ).scalar_one()

    filtered = (
        await session.execute(
            select(func.count(Listing.id)).where(
                Listing.first_seen_at >= since,
                Listing.status == ListingStatus.filtered_out,
            )
        )
    ).scalar_one()

    scams = (
        await session.execute(
            select(func.count(Listing.id)).where(
                Listing.first_seen_at >= since, Listing.status == ListingStatus.scam
            )
        )
    ).scalar_one()

    notified = (
        await session.execute(
            select(func.count(Listing.id)).where(
                Listing.first_seen_at >= since, Listing.status == ListingStatus.notified
            )
        )
    ).scalar_one()

    prefilter_hits = (
        await session.execute(
            select(func.count(Score.id)).where(
                Score.created_at >= since,
                Score.stage == ScoreStage.haiku_filter,
                Score.model == "python_prefilter",
            )
        )
    ).scalar_one()

    haiku_calls = (
        await session.execute(
            select(func.count(Score.id)).where(
                Score.created_at >= since,
                Score.stage == ScoreStage.haiku_filter,
                Score.model != "python_prefilter",
            )
        )
    ).scalar_one()

    sonnet_calls = (
        await session.execute(
            select(func.count(Score.id)).where(
                Score.created_at >= since, Score.stage == ScoreStage.sonnet_pepite
            )
        )
    ).scalar_one()

    total_cost = (
        await session.execute(
            select(func.coalesce(func.sum(ApiUsage.cost_eur), 0.0)).where(
                ApiUsage.created_at >= since
            )
        )
    ).scalar_one()

    cost_per_gem = float(total_cost) / notified if notified else None
    prefilter_rate = (
        prefilter_hits / (prefilter_hits + haiku_calls)
        if (prefilter_hits + haiku_calls) > 0
        else None
    )

    return {
        "window_days": days,
        "listings": {
            "total": int(listings_total),
            "filtered_out": int(filtered),
            "scams": int(scams),
            "notified": int(notified),
        },
        "claude": {
            "total_cost_eur": round(float(total_cost), 4),
            "cost_per_gem_eur": round(cost_per_gem, 4) if cost_per_gem is not None else None,
            "haiku_calls": int(haiku_calls),
            "sonnet_calls": int(sonnet_calls),
        },
        "python_prefilter": {
            "hits": int(prefilter_hits),
            "delegated_to_haiku": int(haiku_calls),
            "hit_rate": round(prefilter_rate, 4) if prefilter_rate is not None else None,
        },
    }

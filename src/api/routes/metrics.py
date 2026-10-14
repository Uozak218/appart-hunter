"""Prometheus-compatible metrics exposition.

Pure-Python, no prometheus_client dep. We aggregate live from Postgres so
scrape-time accuracy wins over in-memory counters (which would reset on
restart).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db
from src.models.listing import Listing
from src.models.score import ApiUsage, Score, ScoreStage
from src.models.scraper_health import ScraperHealth

router = APIRouter(tags=["metrics"])


def _line(name: str, labels: dict[str, str], value: float) -> str:
    if labels:
        rendered = ",".join(f'{k}="{_esc(v)}"' for k, v in sorted(labels.items()))
        return f"{name}{{{rendered}}} {value}"
    return f"{name} {value}"


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(session: AsyncSession = Depends(get_db)) -> str:
    lines: list[str] = []

    # Listings by status + source
    res = await session.execute(
        select(Listing.source, Listing.status, func.count(Listing.id)).group_by(
            Listing.source, Listing.status
        )
    )
    lines.append("# HELP pfh_listings_total Listings observed, by source + status")
    lines.append("# TYPE pfh_listings_total gauge")
    for source, status, count in res.all():
        lines.append(
            _line(
                "pfh_listings_total",
                {"source": str(source), "status": str(status.value if status else "unknown")},
                int(count),
            )
        )

    # Scoring throughput last 24h
    since = datetime.now(UTC) - timedelta(hours=24)
    res = await session.execute(
        select(Score.stage, Score.passed, func.count(Score.id))
        .where(Score.created_at >= since)
        .group_by(Score.stage, Score.passed)
    )
    lines.append("# HELP pfh_scores_24h Scores computed in the last 24h")
    lines.append("# TYPE pfh_scores_24h gauge")
    for stage, passed, count in res.all():
        lines.append(
            _line(
                "pfh_scores_24h",
                {"stage": str(stage.value), "passed": str(bool(passed)).lower()},
                int(count),
            )
        )

    # Alert-worthy sonnet scores last 24h
    res = await session.execute(
        select(func.count(Score.id)).where(
            Score.created_at >= since,
            Score.stage == ScoreStage.sonnet_pepite,
            Score.score >= 70,
        )
    )
    alerts_24h = int(res.scalar_one())
    lines.append("# HELP pfh_alerts_24h Listings that passed the alert threshold in last 24h")
    lines.append("# TYPE pfh_alerts_24h gauge")
    lines.append(_line("pfh_alerts_24h", {}, alerts_24h))

    # Spend by model last 24h + month-to-date
    res = await session.execute(
        select(ApiUsage.model, func.coalesce(func.sum(ApiUsage.cost_eur), 0.0))
        .where(ApiUsage.created_at >= since)
        .group_by(ApiUsage.model)
    )
    lines.append("# HELP pfh_claude_cost_eur_24h EUR spent on Claude in the last 24h")
    lines.append("# TYPE pfh_claude_cost_eur_24h gauge")
    for model, cost in res.all():
        lines.append(_line("pfh_claude_cost_eur_24h", {"model": str(model)}, float(cost)))

    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    res = await session.execute(
        select(func.coalesce(func.sum(ApiUsage.cost_eur), 0.0)).where(
            ApiUsage.created_at >= month_start
        )
    )
    month_spend = float(res.scalar_one())
    lines.append("# HELP pfh_claude_cost_eur_mtd EUR spent on Claude month-to-date")
    lines.append("# TYPE pfh_claude_cost_eur_mtd gauge")
    lines.append(_line("pfh_claude_cost_eur_mtd", {}, month_spend))

    # Scraper health
    res = await session.execute(select(ScraperHealth))
    lines.append("# HELP pfh_scraper_consecutive_failures Consecutive failures per scraper")
    lines.append("# TYPE pfh_scraper_consecutive_failures gauge")
    lines.append("# HELP pfh_scraper_total_runs Total scraper runs")
    lines.append("# TYPE pfh_scraper_total_runs counter")
    lines.append("# HELP pfh_scraper_last_listings_count Listings observed on last run")
    lines.append("# TYPE pfh_scraper_last_listings_count gauge")
    for row in res.scalars().all():
        labels = {"source": row.source}
        lines.append(_line("pfh_scraper_consecutive_failures", labels, row.consecutive_failures))
        lines.append(_line("pfh_scraper_total_runs", labels, row.total_runs))
        lines.append(_line("pfh_scraper_last_listings_count", labels, row.last_listings_count))

    return "\n".join(lines) + "\n"

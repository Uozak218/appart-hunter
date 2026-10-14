"""Prompt-drift detection.

When ``PROMPT_VERSION`` changes we want to understand how the new rubric
affects our previously-scored gems. This job samples recent Sonnet-scored
listings (the ones that scored ≥ alert_threshold), re-runs the current
pipeline against them, and alerts if a meaningful fraction of scores moved.

Used to catch unintended regressions after tuning the Sonnet system prompt.
Not for routine accuracy — just a tripwire.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import SearchCriteria
from src.models.listing import Listing, ListingStatus
from src.models.score import Score, ScoreStage
from src.scoring.pipeline import ScoringPipeline


@dataclass
class DriftReport:
    sample_size: int
    shifted_by_10_or_more: int
    shifted_by_20_or_more: int
    moved_up: int
    moved_down: int
    new_prompt_version: str
    old_prompt_versions: list[str]


async def _sample_recent_gems(
    session: AsyncSession, *, sample_size: int, days: int, min_score: int
) -> list[tuple[Listing, Score]]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        select(Listing, Score)
        .join(
            Score,
            (Score.listing_id == Listing.id) & (Score.stage == ScoreStage.sonnet_pepite),
        )
        .where(
            Score.created_at >= cutoff,
            Score.score >= min_score,
            Listing.status != ListingStatus.gone,
            Listing.status != ListingStatus.scam,
        )
        .order_by(desc(Score.created_at))
        .limit(max(sample_size * 5, 50))  # oversample, then random sample
    )
    rows = list(result.all())
    random.shuffle(rows)
    return rows[:sample_size]


async def detect_drift(
    session: AsyncSession,
    criteria: SearchCriteria,
    *,
    sample_size: int = 10,
    days: int = 30,
    min_score: int = 70,
) -> DriftReport:
    rows = await _sample_recent_gems(
        session, sample_size=sample_size, days=days, min_score=min_score
    )
    if not rows:
        return DriftReport(
            sample_size=0,
            shifted_by_10_or_more=0,
            shifted_by_20_or_more=0,
            moved_up=0,
            moved_down=0,
            new_prompt_version="",
            old_prompt_versions=[],
        )

    pipeline = ScoringPipeline(session, criteria)
    from src.scoring.prompts import PROMPT_VERSION as _V

    shifted10 = 0
    shifted20 = 0
    up = 0
    down = 0
    old_versions: set[str] = set()

    for listing, old_score_row in rows:
        old_v = (old_score_row.raw or {}).get("_prompt_version", "unknown")
        old_versions.add(str(old_v))
        if old_v == _V:
            continue  # already scored with the current prompt → nothing to compare
        # Run the full pipeline; the cache will miss because prompt_version is
        # different, forcing a new Claude call.
        scored = await pipeline.process_listing(listing.id)
        if scored is None:
            continue
        delta = scored.score - (old_score_row.score or 0)
        if abs(delta) >= 10:
            shifted10 += 1
        if abs(delta) >= 20:
            shifted20 += 1
        if delta > 0:
            up += 1
        elif delta < 0:
            down += 1

    return DriftReport(
        sample_size=len(rows),
        shifted_by_10_or_more=shifted10,
        shifted_by_20_or_more=shifted20,
        moved_up=up,
        moved_down=down,
        new_prompt_version=_V,
        old_prompt_versions=sorted(old_versions),
    )


def format_drift_alert(report: DriftReport, threshold_pct: float = 20.0) -> str | None:
    """Return a Telegram-formatted alert string when drift exceeds the threshold, else None."""
    if report.sample_size == 0:
        return None
    pct_shifted = report.shifted_by_10_or_more * 100.0 / report.sample_size
    if pct_shifted < threshold_pct:
        return None
    return (
        f"🔍 *Drift de scoring détecté*\n"
        f"Prompt {' → '.join([*report.old_prompt_versions, report.new_prompt_version])}\n"
        f"\n"
        f"Échantillon: *{report.sample_size}* pépites des 30 derniers jours\n"
        f"• {report.shifted_by_10_or_more} (soit {pct_shifted:.0f}%) ont bougé de ≥ 10 points\n"
        f"• {report.shifted_by_20_or_more} ont bougé de ≥ 20 points\n"
        f"• {report.moved_up} à la hausse  ·  {report.moved_down} à la baisse\n"
        f"\n"
        f"_Vérifie si le nouveau prompt est volontaire; sinon rollback ``PROMPT_VERSION``._"
    )

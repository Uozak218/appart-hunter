"""Score + usage repositories."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.score import ApiUsage, Score, ScoreStage


class ScoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_score(
        self,
        *,
        listing_id: int,
        stage: ScoreStage,
        passed: bool,
        score: int | None,
        reasons: list[str] | None,
        red_flags: list[str] | None,
        highlights: list[str] | None,
        raw: dict | None,
        model: str,
    ) -> Score:
        row = Score(
            listing_id=listing_id,
            stage=stage,
            passed=passed,
            score=score,
            reasons=reasons,
            red_flags=red_flags,
            highlights=highlights,
            raw=raw,
            model=model,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def log_usage(self, usage: ApiUsage) -> None:
        self.session.add(usage)
        await self.session.flush()

    async def month_cost_eur(self, year: int, month: int) -> float:
        result = await self.session.execute(
            select(func.coalesce(func.sum(ApiUsage.cost_eur), 0.0)).where(
                func.extract("year", ApiUsage.created_at) == year,
                func.extract("month", ApiUsage.created_at) == month,
            )
        )
        return float(result.scalar_one())

    async def last_passed_sonnet(self, listing_id: int) -> Score | None:
        result = await self.session.execute(
            select(Score)
            .where(Score.listing_id == listing_id, Score.stage == ScoreStage.sonnet_pepite)
            .order_by(Score.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def latest_sonnet_with_hash(
        self, listing_id: int, content_hash: str, prompt_version: str
    ) -> Score | None:
        """Return the most recent Sonnet score whose raw payload matches content+prompt.

        Used as a cache check before spending tokens on a re-score: if we
        already scored this exact content with this exact prompt version, the
        answer won't have changed.
        """
        result = await self.session.execute(
            select(Score)
            .where(Score.listing_id == listing_id, Score.stage == ScoreStage.sonnet_pepite)
            .order_by(Score.created_at.desc())
            .limit(5)
        )
        for row in result.scalars().all():
            raw = row.raw or {}
            if (
                raw.get("_content_hash") == content_hash
                and raw.get("_prompt_version") == prompt_version
            ):
                return row
        return None

    async def cost_since(self, since: datetime) -> float:
        result = await self.session.execute(
            select(func.coalesce(func.sum(ApiUsage.cost_eur), 0.0)).where(
                ApiUsage.created_at >= since
            )
        )
        return float(result.scalar_one())

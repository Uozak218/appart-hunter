"""Two-stage scoring pipeline orchestrator.

Flow:
1. BudgetManager gate (if month cap reached -> skip scoring entirely).
2. Haiku filter (hard criteria + red flags, text only).
3. If passed -> Sonnet scorer (vision + full criteria), score 0-100.
4. Persist both stages in `scores` table.
5. Return a ScoredListing if alert threshold reached, else None.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import SearchCriteria, get_settings
from src.models.listing import Listing, ListingStatus
from src.models.price_history import PriceHistory
from src.models.score import ScoreStage
from src.schemas.listing import RawListing, ScoredListing
from src.scoring.budget import BudgetManager
from src.scoring.haiku_filter import HaikuFilter
from src.scoring.hard_filter import hard_prefilter
from src.scoring.prompts import PROMPT_VERSION
from src.scoring.sonnet_scorer import SonnetResult, SonnetScorer
from src.storage.repositories.score_repo import ScoreRepository
from src.utils.logging import logger

# Max concurrent Sonnet scorings in a single process. Haiku tier-1 rate limit
# is ~10 000 input tokens/minute and one Haiku call ≈ 3 100 tokens, so >2
# concurrent reliably trips 429. Keep it at 2 — throughput is still ≥30
# scorings/min, plenty for our cadence.
_SCORING_CONCURRENCY = 2
_SCORING_SEMAPHORE: asyncio.Semaphore | None = None


def _get_scoring_semaphore() -> asyncio.Semaphore:
    global _SCORING_SEMAPHORE
    if _SCORING_SEMAPHORE is None:
        _SCORING_SEMAPHORE = asyncio.Semaphore(_SCORING_CONCURRENCY)
    return _SCORING_SEMAPHORE


# Per-arrondissement score calibration: in expensive / cachet-heavy districts,
# "haussmannien" is the baseline so we penalise a tiny bit to compensate for
# Claude's natural drift up. In working-class / modern districts, genuine
# cachet is rarer so we boost when we see it.
_ZONE_NORMALISATION: dict[int, int] = {
    1: -2,
    2: -1,
    3: -1,
    4: -2,
    5: -1,
    6: -3,
    7: -4,
    8: -4,
    9: 0,
    10: +1,
    11: 0,
    12: +1,
    13: +2,
    14: +1,
    15: 0,
    16: -3,
    17: -1,
    18: +2,
    19: +3,
    20: +2,
}


def _normalise_score(raw_score: int, arrondissement: int | None) -> int:
    """Return the arrondissement-normalised score, clamped to [0, 100].

    The shift is small (±4 points max) — we want to nudge Claude's biases,
    not override its visual judgement.
    """
    if arrondissement is None:
        return raw_score
    shift = _ZONE_NORMALISATION.get(arrondissement, 0)
    return max(0, min(100, raw_score + shift))


def _content_hash_for_listing(listing: Listing) -> str:
    import hashlib
    import json as _json

    payload = {
        "title": listing.title or "",
        "description": (listing.description or "")[:4000],
        "rent_cc": listing.rent_cc,
        "surface_m2": listing.surface_m2,
        "photos": sorted(p.url for p in listing.photos),
    }
    return hashlib.sha1(_json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class ScoringPipeline:
    def __init__(
        self,
        session: AsyncSession,
        criteria: SearchCriteria,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self.session = session
        self.criteria = criteria
        settings = get_settings()
        self.client = client or AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.budget = BudgetManager(session)
        self.haiku = HaikuFilter(self.client, criteria, self.budget)
        self.sonnet = SonnetScorer(self.client, criteria, self.budget)
        self.score_repo = ScoreRepository(session)

    async def _load_listing(self, listing_id: int) -> Listing | None:
        result = await self.session.execute(
            select(Listing).options(selectinload(Listing.photos)).where(Listing.id == listing_id)
        )
        return result.scalar_one_or_none()

    async def _price_drop_pct(self, listing: Listing) -> float | None:
        """Return % drop vs. previous observation, or None if no prior price."""
        if listing.rent_cc is None:
            return None
        result = await self.session.execute(
            select(PriceHistory)
            .where(PriceHistory.listing_id == listing.id)
            .order_by(PriceHistory.observed_at.desc())
            .limit(2)
        )
        rows = result.scalars().all()
        if len(rows) < 2:
            return None
        latest = rows[0].rent_cc
        previous = rows[1].rent_cc
        if previous <= 0 or latest >= previous:
            return None
        return round((previous - latest) / previous * 100.0, 2)

    async def process_listing(self, listing_id: int) -> ScoredListing | None:
        # Cap concurrent Sonnet calls so a big scrape can't vaporise the
        # budget in one shot.
        async with _get_scoring_semaphore():
            return await self._process_listing_inner(listing_id)

    async def _process_listing_inner(self, listing_id: int) -> ScoredListing | None:
        listing = await self._load_listing(listing_id)
        if listing is None:
            logger.warning(f"Listing {listing_id} not found for scoring")
            return None

        # Budget check with a safety margin: we block scoring once we're
        # within ~0.10€ of the cap, so that concurrent scorings finishing
        # within the same millisecond can't meaningfully overshoot.
        spent, cap, _ = await self.budget.budget_state()
        if spent >= cap - 0.10:
            logger.warning(
                f"Anthropic monthly cap near/reached ({spent:.2f}/{cap:.2f} EUR) — skipping scoring."
            )
            return None

        # Stage 0 — deterministic Python prefilter (no Claude tokens).
        decision, reasons = hard_prefilter(listing, self.criteria)
        if decision is False:
            listing.status = ListingStatus.filtered_out
            await self.score_repo.save_score(
                listing_id=listing.id,
                stage=ScoreStage.haiku_filter,
                passed=False,
                score=None,
                reasons=reasons,
                red_flags=None,
                highlights=None,
                raw={"source": "python_prefilter"},
                model="python_prefilter",
            )
            await self.session.commit()
            logger.info(f"Listing {listing.id} filtered out by Python prefilter: {reasons[:2]}")
            return None

        if decision is True:
            # Skip Haiku entirely — save the tokens.
            await self.score_repo.save_score(
                listing_id=listing.id,
                stage=ScoreStage.haiku_filter,
                passed=True,
                score=None,
                reasons=reasons,
                red_flags=None,
                highlights=None,
                raw={"source": "python_prefilter"},
                model="python_prefilter",
            )
        else:
            # decision is None — ambiguous, delegate to Haiku
            try:
                haiku_res = await self.haiku.evaluate(listing)
            except Exception:
                return None

            # Always surface Haiku's verdict with its reasoning + confidence so
            # tuning the hard criteria is observable from the logs alone.
            verdict = "PASS" if haiku_res.passes_hard_filter else "FAIL"
            thinking = " [thinking]" if haiku_res.raw.get("_refined_with_thinking") else ""
            logger.info(
                f"🤖 Haiku {verdict}{thinking} listing={listing.id} "
                f'"{(listing.title or "")[:60]}" '
                f"conf={haiku_res.confidence:.2f} "
                f"reasons={haiku_res.reasons[:2]}"
                + (f" red={haiku_res.red_flags[:2]}" if haiku_res.red_flags else "")
            )

            await self.score_repo.save_score(
                listing_id=listing.id,
                stage=ScoreStage.haiku_filter,
                passed=haiku_res.passes_hard_filter,
                score=None,
                reasons=haiku_res.reasons,
                red_flags=haiku_res.red_flags,
                highlights=None,
                raw=haiku_res.raw,
                model=self.haiku.model,
            )

            if not haiku_res.passes_hard_filter:
                listing.status = ListingStatus.filtered_out
                await self.session.commit()
                return None

        # Stage 2 — Sonnet (vision), with content-hash + prompt-version cache.
        content_hash = _content_hash_for_listing(listing)
        cached = await self.score_repo.latest_sonnet_with_hash(
            listing.id, content_hash, PROMPT_VERSION
        )
        if cached is not None:
            logger.info(
                f"📦 Sonnet cache-HIT listing={listing.id} "
                f'"{(listing.title or "")[:60]}" score={cached.score} '
                f"(hash={content_hash[:8]})"
            )
            sonnet_res = SonnetResult(
                score=int(cached.score or 0),
                urgent=bool((cached.raw or {}).get("urgent", False)),
                highlights=list(cached.highlights or []),
                red_flags=list(cached.red_flags or []),
                reasons=list(cached.reasons or []),
                summary_fr=str((cached.raw or {}).get("summary_fr", "")),
                raw={**(cached.raw or {}), "_cached": True},
            )
        else:
            try:
                sonnet_res = await self.sonnet.score(listing)
            except Exception:
                await self.session.commit()
                return None
            # Stamp the content hash on the raw payload so the cache works on next run.
            sonnet_res.raw["_content_hash"] = content_hash

        # Arrondissement-level score normalisation: a small nudge to correct
        # Claude's bias toward cachet-heavy districts and reward real gems in
        # working-class districts. Stored in raw so it's traceable.
        raw_score = sonnet_res.score
        normalised = _normalise_score(raw_score, listing.arrondissement)
        if normalised != raw_score:
            sonnet_res.raw["_raw_score"] = raw_score
            sonnet_res.raw["_normalised"] = normalised
            sonnet_res.score = normalised

        # Log Sonnet's verdict with the full rationale so you can read what
        # Claude actually thought about each listing.
        emoji = "🔥" if sonnet_res.score >= 85 else "✨" if sonnet_res.score >= 70 else "➖"
        thinking = " [thinking]" if sonnet_res.raw.get("_refined_with_thinking") else ""
        norm_tag = f" (raw {raw_score}→{normalised})" if normalised != raw_score else ""
        logger.info(
            f"{emoji} Sonnet score={sonnet_res.score}/100{norm_tag}{thinking} "
            f"listing={listing.id} "
            f'"{(listing.title or "")[:60]}"'
        )
        if sonnet_res.highlights:
            logger.info(f"   ✅ atouts: {', '.join(sonnet_res.highlights[:5])}")
        if sonnet_res.red_flags:
            logger.info(f"   ⚠️  red flags: {', '.join(sonnet_res.red_flags[:3])}")
        if sonnet_res.summary_fr:
            logger.info(f'   🗨️  "{sonnet_res.summary_fr[:200]}"')

        await self.score_repo.save_score(
            listing_id=listing.id,
            stage=ScoreStage.sonnet_pepite,
            passed=True,
            score=sonnet_res.score,
            reasons=sonnet_res.reasons,
            red_flags=sonnet_res.red_flags,
            highlights=sonnet_res.highlights,
            raw=sonnet_res.raw,
            model=self.sonnet.model,
        )

        listing.status = ListingStatus.scored
        await self.session.commit()

        alert_threshold = self.criteria.scoring.alert_threshold
        urgent_threshold = self.criteria.scoring.urgent_threshold
        if sonnet_res.score < alert_threshold:
            logger.debug(
                f"Listing {listing.id} scored {sonnet_res.score} (<{alert_threshold}), no alert."
            )
            return None

        price_drop = await self._price_drop_pct(listing)

        raw_listing = RawListing(
            source=listing.source,
            external_id=listing.external_id,
            url=listing.url,
            title=listing.title,
            description=listing.description,
            rent_cc=listing.rent_cc,
            charges=listing.charges,
            surface_m2=listing.surface_m2,
            rooms=listing.rooms,
            property_type=listing.property_type,
            furnished=listing.furnished,
            floor=listing.floor,
            has_elevator=listing.has_elevator,
            postal_code=listing.postal_code,
            address=listing.address,
            photo_urls=[p.url for p in sorted(listing.photos, key=lambda p: p.position)],
        )
        summary = sonnet_res.summary_fr or " ".join(sonnet_res.reasons[:2])
        merged_reasons = [r for r in (sonnet_res.reasons + [summary]) if r]

        return ScoredListing(
            listing_id=listing.id,
            raw=raw_listing,
            score=sonnet_res.score,
            highlights=sonnet_res.highlights,
            red_flags=sonnet_res.red_flags,
            reasons=merged_reasons,
            is_urgent=sonnet_res.urgent or sonnet_res.score >= urgent_threshold,
            price_drop_pct=price_drop,
            furnished_status=sonnet_res.furnished_status,
        )

    async def budget_soft_warn(self) -> tuple[bool, float, float]:
        """Returns (should_warn, spent_eur, cap_eur)."""
        spent, cap, pct = await self.budget.budget_state()
        return pct >= 80.0, spent, cap

    async def recent_spend(self, hours: int = 1) -> float:
        since = datetime.now(UTC) - timedelta(hours=hours)
        return await self.score_repo.cost_since(since)

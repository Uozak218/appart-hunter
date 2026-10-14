"""Anthropic cost tracking + hard cap.

We compute a EUR cost from the ``Usage`` block Anthropic returns for each call,
store it in ``api_usage``, and consult the month-to-date total before scoring
new listings. When the cap is hit, scoring is disabled (but scraping keeps
running so we don't miss listings once the next month starts / the cap is
raised).

The ``allow_scoring`` check applies a small safety margin (0.10 EUR) so
concurrent scorings finishing simultaneously cannot materially overshoot the
monthly cap.

Pricing is hard-coded per model family and can be overridden per deployment
via env vars if Anthropic publishes new rates.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.score import ApiUsage
from src.storage.repositories.score_repo import ScoreRepository
from src.utils.logging import logger

# Per-million-token USD pricing. Kept conservative — override via env if needed.
# Claude 4.x pricing is (roughly) Opus > Sonnet > Haiku. Cached reads are ~0.1x.
DEFAULT_PRICING_USD: dict[str, dict[str, float]] = {
    "haiku": {
        "input": 1.0,
        "output": 5.0,
        "cache_write": 1.25,
        "cache_read": 0.1,
    },
    "sonnet": {
        "input": 3.0,
        "output": 15.0,
        "cache_write": 3.75,
        "cache_read": 0.3,
    },
    "opus": {
        "input": 15.0,
        "output": 75.0,
        "cache_write": 18.75,
        "cache_read": 1.5,
    },
}


def _model_family(model: str) -> str:
    m = model.lower()
    if "haiku" in m:
        return "haiku"
    if "opus" in m:
        return "opus"
    return "sonnet"


@dataclass
class AnthropicUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @classmethod
    def from_response(cls, usage: object | None) -> AnthropicUsage:
        if usage is None:
            return cls()

        def _get(key: str) -> int:
            if isinstance(usage, dict):
                return int(usage.get(key, 0) or 0)
            return int(getattr(usage, key, 0) or 0)

        return cls(
            input_tokens=_get("input_tokens"),
            output_tokens=_get("output_tokens"),
            cache_creation_input_tokens=_get("cache_creation_input_tokens"),
            cache_read_input_tokens=_get("cache_read_input_tokens"),
        )


def compute_cost_usd(model: str, usage: AnthropicUsage) -> float:
    family = _model_family(model)
    pricing = DEFAULT_PRICING_USD[family]

    def _env(k: str, default: float) -> float:
        raw = os.getenv(f"PRICE_{family.upper()}_{k.upper()}")
        try:
            return float(raw) if raw else default
        except ValueError:
            return default

    pi = _env("input", pricing["input"])
    po = _env("output", pricing["output"])
    pcw = _env("cache_write", pricing["cache_write"])
    pcr = _env("cache_read", pricing["cache_read"])

    return (
        usage.input_tokens * pi
        + usage.output_tokens * po
        + usage.cache_creation_input_tokens * pcw
        + usage.cache_read_input_tokens * pcr
    ) / 1_000_000.0


class BudgetManager:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ScoreRepository(session)
        self.settings = get_settings()

    async def record_usage(
        self,
        *,
        model: str,
        stage: str,
        usage: AnthropicUsage,
        listing_id: int | None = None,
        error: str | None = None,
    ) -> ApiUsage:
        cost_usd = compute_cost_usd(model, usage)
        cost_eur = cost_usd * self.settings.usd_eur_rate
        row = ApiUsage(
            model=model,
            stage=stage,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
            cost_usd=cost_usd,
            cost_eur=cost_eur,
            listing_id=listing_id,
            error=error,
        )
        await self.repo.log_usage(row)
        # One concise cost line per Anthropic call — makes the price of each
        # listing visible in the scraper log without grepping /metrics.
        if error:
            logger.warning(f"💸 {model} [{stage}] FAILED listing={listing_id} — {error[:120]}")
        else:
            total_in = (
                usage.input_tokens
                + usage.cache_creation_input_tokens
                + usage.cache_read_input_tokens
            )
            cache_hit = (
                f" cache_hit={usage.cache_read_input_tokens}"
                if usage.cache_read_input_tokens
                else ""
            )
            logger.info(
                f"💸 {model} [{stage}] listing={listing_id} "
                f"in={total_in}{cache_hit} out={usage.output_tokens} "
                f"→ {cost_eur * 100:.2f} cents"
            )
        return row

    async def month_to_date_eur(self) -> float:
        now = datetime.now(UTC)
        return await self.repo.month_cost_eur(now.year, now.month)

    async def budget_state(self) -> tuple[float, float, float]:
        """Return (spent_eur, cap_eur, pct)."""
        cap = self.settings.anthropic_monthly_budget_eur
        spent = await self.month_to_date_eur()
        pct = (spent / cap) * 100.0 if cap > 0 else 0.0
        return spent, cap, pct

    async def allow_scoring(self) -> bool:
        spent, cap, _ = await self.budget_state()
        # Block a little before the cap to absorb in-flight concurrent calls.
        return spent < cap - 0.10

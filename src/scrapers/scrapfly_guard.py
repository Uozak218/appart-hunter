"""Scrapfly credit guard.

Polls the Scrapfly account API; when the billing period's credit burn runs ahead
of a sustainable pace, it PAUSES the Scrapfly-backed sources (leboncoin, seloger,
pap, logic_immo) and alerts Telegram. The free residential sources keep running.
When the pace recovers (or a new period starts), it resumes and alerts.

This is the backstop that makes a mis-set cadence survivable: a runaway burn
trips the guard within minutes (the next guard tick) instead of draining the
whole month's budget in an hour. The account endpoint is free (not billed).
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from src.config import get_settings
from src.utils.logging import logger

ACCOUNT_URL = "https://api.scrapfly.io/account?key={key}"

_paused: bool = False


def is_scrapfly_paused() -> bool:
    return _paused


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


async def fetch_account() -> dict | None:
    """Return ``{used, limit, period_start, period_end}`` or None on any error."""
    settings = get_settings()
    if not settings.scrapfly_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(ACCOUNT_URL.format(key=settings.scrapfly_api_key))
            r.raise_for_status()
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[scrapfly-guard] account fetch failed: {exc}")
        return None
    sub = data.get("subscription", {}) or {}
    usage = (sub.get("usage", {}) or {}).get("scrape", {}) or sub.get("usage", {}) or {}
    period = sub.get("period", {}) or {}
    return {
        "used": usage.get("current"),
        "limit": usage.get("limit"),
        "period_start": _parse_dt(period.get("start")),
        "period_end": _parse_dt(period.get("end")),
    }


def evaluate_overspend(
    *,
    used: int | None,
    limit: int | None,
    period_start: datetime | None,
    period_end: datetime | None,
    now: datetime,
    ratio: float,
    min_used_frac: float = 0.15,
) -> tuple[bool, float]:
    """Pure: is the burn ahead of pace? Returns ``(over, expected_used_now)``.

    ``expected`` = (fraction of the period elapsed) x limit. We trip when
    ``used > expected * ratio``, but only once at least ``min_used_frac`` (15%)
    of the budget is spent — so a one-time early-period burst (e.g. the backlog
    re-fetched right after a billing reset) can't false-trip the guard. A true
    runaway still crosses 15% within the hour and trips.
    """
    if not used or not limit or not period_start or not period_end:
        return False, 0.0
    total = (period_end - period_start).total_seconds()
    elapsed = (now - period_start).total_seconds()
    if total <= 0 or elapsed <= 0:
        return False, 0.0
    expected = min(1.0, elapsed / total) * limit
    if used < limit * min_used_frac:
        return False, expected
    return used > expected * ratio, expected


async def scrapfly_guard_job() -> None:
    """Periodic guard: pause/resume Scrapfly sources + alert on transitions."""
    global _paused
    settings = get_settings()
    if not settings.scrapfly_guard_enabled:
        return
    acct = await fetch_account()
    if acct is None:
        return
    over, expected = evaluate_overspend(
        used=acct["used"],
        limit=acct["limit"],
        period_start=acct["period_start"],
        period_end=acct["period_end"],
        now=datetime.now(UTC),
        ratio=settings.scrapfly_guard_ratio,
    )

    from src.notifier.queue import get_notification_queue

    notifier = get_notification_queue()
    if over and not _paused:
        _paused = True
        used, limit = acct["used"], acct["limit"]
        await notifier.send_system(
            "🛑 *Garde-fou Scrapfly déclenché* — le burn est trop rapide.\n"
            f"Utilisé *{used:,} / {limit:,}* crédits (rythme attendu ~{int(expected):,}).\n"
            "Sources DataDome (leboncoin / seloger / pap) **mises en PAUSE** pour "
            "protéger le budget du mois. Reprise auto dès que le rythme repasse "
            "sous le seuil (ou au prochain cycle de facturation). Si ça se répète, "
            "ralentis la cadence.",
            urgent=True,
        )
        logger.warning(
            f"[scrapfly-guard] TRIPPED: used={used} expected~{int(expected)} → Scrapfly paused"
        )
    elif not over and _paused:
        _paused = False
        await notifier.send_system(
            "✅ *Garde-fou Scrapfly levé* — le rythme est repassé sous le seuil, "
            "sources DataDome réactivées."
        )
        logger.info("[scrapfly-guard] recovered → Scrapfly resumed")

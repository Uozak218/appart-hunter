"""Daily heartbeat — quick 'I'm alive' message pushed to Telegram.

Sends a compact 24h digest of what the bot did so you can tell at a glance
that the scheduler is running even when no gems were found.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.listing import Listing, ListingStatus
from src.models.score import ApiUsage, Score, ScoreStage
from src.models.scraper_health import ScraperHealth
from src.notifier.queue import get_notification_queue
from src.notifier.templates import SOURCE_LABEL


@dataclass
class HeartbeatReport:
    seen_24h: int
    scored_24h: int
    alerts_24h: int
    scams_24h: int
    by_source: list[tuple[str, int]]
    scraper_health: list[tuple[str, int, int]]  # (source, consecutive_failures, last_count)
    claude_cost_eur_24h: float
    # New: why things got rejected + how close the survivors got.
    top_reject_reasons: list[tuple[str, int]]  # ("meuble", 13), …
    score_buckets: list[tuple[str, int]]  # ("70-100", 0), ("50-69", 3), …
    near_misses: list[tuple[int, str, str, str, int, float, float]]
    #   (score, source, postcode, title, rooms, surface, rent_cc)


async def gather_heartbeat(session: AsyncSession) -> HeartbeatReport:
    since = datetime.now(UTC) - timedelta(hours=24)

    seen = (
        await session.execute(select(func.count(Listing.id)).where(Listing.first_seen_at >= since))
    ).scalar_one()

    by_source_res = await session.execute(
        select(Listing.source, func.count(Listing.id))
        .where(Listing.first_seen_at >= since)
        .group_by(Listing.source)
    )
    by_source = sorted(by_source_res.all(), key=lambda r: -r[1])

    scored = (
        await session.execute(
            select(func.count(Score.id)).where(
                Score.created_at >= since,
                Score.stage == ScoreStage.sonnet_pepite,
            )
        )
    ).scalar_one()

    alerts = (
        await session.execute(
            select(func.count(Score.id)).where(
                Score.created_at >= since,
                Score.stage == ScoreStage.sonnet_pepite,
                Score.score >= 70,
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

    cost = (
        await session.execute(
            select(func.coalesce(func.sum(ApiUsage.cost_eur), 0.0)).where(
                ApiUsage.created_at >= since
            )
        )
    ).scalar_one()

    health_res = await session.execute(select(ScraperHealth).order_by(ScraperHealth.source))
    scraper_health = [
        (row.source, row.consecutive_failures, row.last_listings_count)
        for row in health_res.scalars().all()
    ]

    # Top reject reasons — combine Python-prefilter `reasons` + Haiku `red_flags`
    # so the user sees *why* the bot rejected so many ads. Truncate strings so
    # the Telegram message stays readable.
    from collections import Counter

    reject_rows = (
        await session.execute(
            select(Score.reasons, Score.red_flags).where(
                Score.created_at >= since,
                Score.stage == ScoreStage.haiku_filter,
                Score.passed.is_(False),
            )
        )
    ).all()
    reject_counter: Counter[str] = Counter()
    for reasons, red_flags in reject_rows:
        for rf in red_flags or []:
            reject_counter[rf.split("—")[0].strip()[:40]] += 1
        if not (red_flags):  # fall back to first reason if no red flags
            for r in (reasons or [])[:1]:
                reject_counter[r[:40]] += 1
    top_rejects = reject_counter.most_common(5)

    # Sonnet score distribution
    score_rows = (
        await session.execute(
            select(Score.score).where(
                Score.created_at >= since,
                Score.stage == ScoreStage.sonnet_pepite,
                Score.score.isnot(None),
            )
        )
    ).all()
    scores = [int(s[0]) for s in score_rows]
    buckets = [("85-100", 0), ("70-84", 0), ("50-69", 0), ("30-49", 0), ("0-29", 0)]
    for sc in scores:
        if sc >= 85:
            buckets[0] = (buckets[0][0], buckets[0][1] + 1)
        elif sc >= 70:
            buckets[1] = (buckets[1][0], buckets[1][1] + 1)
        elif sc >= 50:
            buckets[2] = (buckets[2][0], buckets[2][1] + 1)
        elif sc >= 30:
            buckets[3] = (buckets[3][0], buckets[3][1] + 1)
        else:
            buckets[4] = (buckets[4][0], buckets[4][1] + 1)

    # Near misses — top 3 Sonnet scores in 24h that didn't reach 70.
    nm_rows = (
        await session.execute(
            select(
                Score.score,
                Listing.source,
                Listing.postal_code,
                Listing.title,
                Listing.rooms,
                Listing.surface_m2,
                Listing.rent_cc,
            )
            .join(Listing, Listing.id == Score.listing_id)
            .where(
                Score.created_at >= since,
                Score.stage == ScoreStage.sonnet_pepite,
                Score.score.isnot(None),
                Score.score < 70,
            )
            .order_by(Score.score.desc())
            .limit(3)
        )
    ).all()
    near_misses = [
        (
            int(r[0]),
            str(r[1] or ""),
            str(r[2] or "?"),
            str(r[3] or "")[:50],
            int(r[4] or 0),
            float(r[5] or 0),
            float(r[6] or 0),
        )
        for r in nm_rows
    ]

    return HeartbeatReport(
        seen_24h=int(seen),
        scored_24h=int(scored),
        alerts_24h=int(alerts),
        scams_24h=int(scams),
        by_source=list(by_source),
        scraper_health=scraper_health,
        claude_cost_eur_24h=float(cost),
        top_reject_reasons=top_rejects,
        score_buckets=buckets,
        near_misses=near_misses,
    )


def format_heartbeat(report: HeartbeatReport) -> str:
    if not report.scraper_health:
        return "💓 *Paris Flat Hunter — heartbeat*\n\n_Rien n'a encore tourné._"

    lines = [
        "💓 *Appart Hunter — résumé 24h*",
        "",
        f"📥 *{report.seen_24h}* annonces vues · "
        f"🧠 *{report.scored_24h}* scorées · "
        f"✨ *{report.alerts_24h}* pépites · "
        f"🚫 *{report.scams_24h}* arnaques · "
        f"💸 *{report.claude_cost_eur_24h:.2f} €*",
    ]

    # Pareto by source — collapse to top 8 to keep the message compact.
    if report.by_source:
        top = report.by_source[:8]
        rest = sum(c for _, c in report.by_source[8:])
        chunks = [f"{SOURCE_LABEL.get(s, s)} {c}" for s, c in top]
        if rest:
            chunks.append(f"autres {rest}")
        lines.append("_par source_ · " + " · ".join(chunks))

    # Distribution des scores Sonnet — montre les near-misses même sans pépite.
    if any(n for _, n in report.score_buckets):
        lines.append("")
        lines.append("*Distribution Sonnet*")
        for label, n in report.score_buckets:
            if n:
                emoji = "🔥" if label.startswith("85") else "✨" if label.startswith("70") else "➖"
                lines.append(f"  {emoji} {label}: *{n}*")

    # Pourquoi tant filtré — top 5 raisons.
    if report.top_reject_reasons:
        lines.append("")
        lines.append("*Top motifs de rejet (Haiku)*")
        for reason, n in report.top_reject_reasons:
            lines.append(f"  • {n}× `{reason}`")

    # Near-misses : top 3 annonces qui ont scoré mais sans atteindre 70.
    if report.near_misses:
        lines.append("")
        lines.append("*Near-misses* (Sonnet < 70)")
        for sc, src, pc, title, rooms, surf, rent in report.near_misses:
            t_label = f"T{rooms}" if rooms else "?"
            lines.append(
                f"  ➖ *{sc}*/100 · {SOURCE_LABEL.get(src, src)} {pc} · "
                f"{t_label} {surf:.0f}m² {rent:.0f}€ · {title}"
            )

    # Sources health — collapse healthy ones into a single line.
    lines.append("")
    healthy = [s for s, fails, _ in report.scraper_health if fails == 0]
    broken = [(s, fails, last) for s, fails, last in report.scraper_health if fails > 0]
    if healthy:
        lines.append(f"🟢 {len(healthy)} sources OK")
    for source, fails, last_count in broken:
        emoji = "🟡" if fails < 3 else "🔴"
        lines.append(
            f"{emoji} {SOURCE_LABEL.get(source, source)} — {last_count} ann. / {fails} fails"
        )

    return "\n".join(lines)


async def send_heartbeat(session: AsyncSession) -> None:
    report = await gather_heartbeat(session)
    text = format_heartbeat(report)
    notifier = get_notification_queue()
    await notifier.send_system(text, urgent=False)

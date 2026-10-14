"""Weekly digest of the Paris market — pushed every Sunday evening."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.listing import Listing, ListingStatus
from src.models.score import ApiUsage, Score, ScoreStage
from src.models.scraper_health import ScraperHealth
from src.notifier.queue import get_notification_queue
from src.notifier.templates import SOURCE_LABEL


async def _gather_digest(session: AsyncSession) -> str:
    now = datetime.now(UTC)
    since = now - timedelta(days=7)

    # Total listings + by source
    total_res = await session.execute(
        select(Listing.source, func.count(Listing.id))
        .where(Listing.first_seen_at >= since)
        .group_by(Listing.source)
    )
    by_source = sorted(total_res.all(), key=lambda r: -r[1])
    total = sum(c for _, c in by_source)

    # Alerts (sonnet score >= 70)
    alerts_res = await session.execute(
        select(func.count(Score.id)).where(
            Score.created_at >= since,
            Score.stage == ScoreStage.sonnet_pepite,
            Score.score >= 70,
        )
    )
    alerts = int(alerts_res.scalar_one())

    # Urgent
    urgent_res = await session.execute(
        select(func.count(Score.id)).where(
            Score.created_at >= since,
            Score.stage == ScoreStage.sonnet_pepite,
            Score.score >= 85,
        )
    )
    urgent = int(urgent_res.scalar_one())

    # Top 3 gems of the week (by Sonnet score)
    top_res = await session.execute(
        select(Listing, Score)
        .join(
            Score,
            (Score.listing_id == Listing.id) & (Score.stage == ScoreStage.sonnet_pepite),
        )
        .where(Score.created_at >= since, Score.score >= 70)
        .order_by(desc(Score.score))
        .limit(5)
    )
    top = list(top_res.all())

    # Market price stats per arrondissement (median rent_cc / surface)
    market_res = await session.execute(
        select(
            Listing.arrondissement,
            func.percentile_cont(0.5).within_group(
                Listing.rent_cc / func.nullif(Listing.surface_m2, 0)
            ),
            func.count(Listing.id),
        )
        .where(
            Listing.first_seen_at >= since,
            Listing.surface_m2.is_not(None),
            Listing.rent_cc.is_not(None),
            Listing.surface_m2 > 0,
            Listing.arrondissement.is_not(None),
        )
        .group_by(Listing.arrondissement)
        .order_by(Listing.arrondissement)
    )
    market = list(market_res.all())

    # Spend
    spend_res = await session.execute(
        select(func.coalesce(func.sum(ApiUsage.cost_eur), 0.0)).where(ApiUsage.created_at >= since)
    )
    week_cost = float(spend_res.scalar_one())

    # Scraper health summary
    health_res = await session.execute(select(ScraperHealth))
    health_lines = []
    for row in health_res.scalars().all():
        emoji = (
            "🟢"
            if row.consecutive_failures == 0
            else ("🟡" if row.consecutive_failures < 3 else "🔴")
        )
        health_lines.append(f"{emoji} {row.source}: {row.total_runs} runs")

    # User interests
    fav_res = await session.execute(
        select(Listing.user_interest, func.count(Listing.id))
        .where(Listing.first_seen_at >= since)
        .group_by(Listing.user_interest)
    )
    interests = {ui.value if ui else "none": int(c) for ui, c in fav_res.all()}

    # Scams
    scam_res = await session.execute(
        select(func.count(Listing.id)).where(
            Listing.first_seen_at >= since, Listing.status == ListingStatus.scam
        )
    )
    scams = int(scam_res.scalar_one())

    # ---- Render --------------------------------------------------------
    lines = [
        "*📊 Récap hebdo Paris Flat Hunter*",
        f"_du {since.strftime('%d/%m')} au {now.strftime('%d/%m')}_",
        "",
        f"📥 *{total}* annonces vues "
        f"({', '.join(f'{SOURCE_LABEL.get(s, s)} {c}' for s, c in by_source)})",
        f"✨ *{alerts}* pépites (>= 70/100), dont 🔥 *{urgent}* urgentes",
        f"🚫 *{scams}* arnaques filtrées",
    ]

    if interests:
        bits = []
        if interests.get("interested"):
            bits.append(f"❤️ {interests['interested']} intéressé")
        if interests.get("contacted"):
            bits.append(f"📞 {interests['contacted']} contacté")
        if interests.get("not_interested"):
            bits.append(f"🚫 {interests['not_interested']} ignoré")
        if bits:
            lines.append("👤 " + "  ·  ".join(bits))

    lines.append("")
    if top:
        lines.append("*🏆 Top pépites*")
        for listing, score in top:
            arr = f"75{listing.arrondissement:02d}" if listing.arrondissement else "—"
            price = f"{int(listing.rent_cc)} €" if listing.rent_cc else "—"
            surface = f"{int(listing.surface_m2)} m²" if listing.surface_m2 else "—"
            lines.append(
                f"• [{score.score}/100]({listing.url}) — {listing.title[:50]} "
                f"({arr}, {price}, {surface})"
            )
        lines.append("")

    if market:
        lines.append("*💶 Prix médian €/m² (charges comprises)*")
        for arr, price, n in market:
            if price is None:
                continue
            lines.append(f"• Paris {int(arr):02d}e : {float(price):.1f} €/m²  _(n={n})_")
        lines.append("")

    lines.append("*⚙️ État des scrapers*")
    lines.extend(health_lines)
    lines.append("")
    lines.append(f"💸 Coût Claude semaine : *{week_cost:.2f} €*")
    return "\n".join(lines)


async def send_weekly_digest(session: AsyncSession) -> None:
    text = await _gather_digest(session)
    notifier = get_notification_queue()
    await notifier.send_system(text, urgent=False)

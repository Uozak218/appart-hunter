"""APScheduler job definitions.

Each source runs on a jittered interval. The scheduler invokes
``run_scraper`` which:
  1. Runs the scraper in a fresh AsyncSession.
  2. Upserts every RawListing (tracking dedup + price changes).
  3. Triggers the scoring pipeline for every new/changed listing.
  4. Pushes alerts onto the notification queue.

Budget + scraper-health alerts run on their own schedule.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func, select

from src.config import SearchCriteria, get_criteria, get_settings
from src.models.listing import ListingStatus
from src.models.scraper_health import ScraperHealth
from src.notifier.queue import NotificationQueue, get_notification_queue
from src.schemas.listing import RawListing
from src.scoring.photo_age import detect_photo_age
from src.scoring.pipeline import ScoringPipeline
from src.scoring.poster_detector import detect_poster
from src.scoring.scam_detector import detect_scam
from src.scrapers import SCRAPER_REGISTRY
from src.storage.database import get_session_factory
from src.storage.dedup import find_duplicate
from src.storage.repositories.listing_repo import ListingRepository
from src.utils.geo import is_in_target_zone
from src.utils.logging import logger

SOURCE_INTERVALS_ATTR = {
    "leboncoin": "scrape_interval_leboncoin",
    "seloger": "scrape_interval_seloger",
    "pap": "scrape_interval_pap",
    "locservice": "scrape_interval_locservice",
    "paris_gestion": "scrape_interval_paris_gestion",
    "bienici": "scrape_interval_bienici",
    "logic_immo": "scrape_interval_logic_immo",
    "superimmo": "scrape_interval_superimmo",
    "foncia": "scrape_interval_foncia",
    "glf": "scrape_interval_glf",
    "gtf": "scrape_interval_gtf",
    "figaro": "scrape_interval_figaro",
    "orpi": "scrape_interval_orpi",
    "century21": "scrape_interval_century21",
    "laforet": "scrape_interval_laforet",
    "guyhoquet": "scrape_interval_guyhoquet",
    "nestenn": "scrape_interval_nestenn",
    "espaces_atypiques": "scrape_interval_espaces_atypiques",
    "junot": "scrape_interval_junot",
    "barnes": "scrape_interval_barnes",
    "engelvoelkers": "scrape_interval_engelvoelkers",
    "parisseine": "scrape_interval_parisseine",
    "district": "scrape_interval_district",
    "breteuil": "scrape_interval_breteuil",
    "cabinetroche": "scrape_interval_cabinetroche",
}


async def _passes_zone_gate(raw: RawListing, criteria: SearchCriteria) -> bool:
    return is_in_target_zone(
        criteria, postcode=raw.postal_code, lat=raw.latitude, lon=raw.longitude
    )


async def _persist_seen_rejected(
    repo: ListingRepository, session, raw: RawListing, status: ListingStatus
) -> None:
    """Record a rejected listing so its ``external_id`` is 'seen' and is NOT
    re-fetched on every later cycle.

    Without this, listings dropped pre-upsert (out-of-zone, cross-site dup) are
    never saved → never returned by ``_already_seen_external_ids`` → re-fetched
    (and, for Scrapfly-backed sources, re-billed) on every single poll. Saving a
    minimal row with a terminal status plugs that leak.
    """
    listing, _, _ = await repo.upsert(raw)
    listing.status = status
    await session.commit()


async def run_scraper(source: str) -> None:
    cls = SCRAPER_REGISTRY[source]
    from src.scheduler.window import is_within_scrape_window

    if not is_within_scrape_window():
        logger.debug(f"[{source}] skip — hors fenêtre de scraping active")
        return
    if cls.use_scrapfly:
        from src.scrapers.scrapfly_guard import is_scrapfly_paused

        if is_scrapfly_paused():
            logger.warning(f"[{source}] skip — garde-fou crédits Scrapfly actif")
            return
    criteria = get_criteria()
    factory = get_session_factory()
    notifier = get_notification_queue()

    # Lightweight counters rendered in a single summary line at the end of the run.
    stats = {
        "seen": 0,
        "new_raw": 0,
        "out_of_zone": 0,
        "cross_site_dup": 0,
        "scam": 0,
        "to_score": 0,
    }

    async with factory() as session:
        scraper = cls(session, criteria)
        outcome = await scraper.run()
        stats["seen"] = outcome.seen_count
        stats["new_raw"] = len(outcome.new_listings)
        if not outcome.new_listings:
            logger.info(f"[{source}] ↻ {outcome.seen_count} seen, 0 new")
            return

        listing_ids_to_score: list[int] = []
        listing_repo = ListingRepository(session)

        # pre-fetch existing dedup candidates (within same postal code + price range)
        dedup_candidates_by_raw: dict[str, list] = {}
        for raw in outcome.new_listings:
            # Cheap poster-type tag so downstream (templates, contact flow) can adapt.
            if raw.poster_type is None:
                raw.poster_type = detect_poster(
                    title=raw.title, description=raw.description, raw=raw.raw
                )

            if not await _passes_zone_gate(raw, criteria):
                stats["out_of_zone"] += 1
                await _persist_seen_rejected(listing_repo, session, raw, ListingStatus.filtered_out)
                continue

            # Look for cross-site dups with the same dedup bucket
            from src.utils.hashing import dedup_key

            key = dedup_key(
                title=raw.title,
                surface=raw.surface_m2,
                rent_cc=raw.rent_cc,
                postal_code=raw.postal_code,
            )
            if key not in dedup_candidates_by_raw:
                candidates_map = await listing_repo.recent_dedup_keys([key])
                dedup_candidates_by_raw[key] = (
                    list(candidates_map.values()) if candidates_map else []
                )
            dup = find_duplicate(raw, dedup_candidates_by_raw[key])
            if dup is not None:
                stats["cross_site_dup"] += 1
                await _persist_seen_rejected(listing_repo, session, raw, ListingStatus.filtered_out)
                continue

            listing, created, price_changed = await listing_repo.upsert(raw)

            # Run cheap heuristic scam detection BEFORE we burn Claude tokens.
            scam = detect_scam(raw, market_prices=criteria.market_prices)

            # EXIF-based photo-age check — adds a scam signal when the newest
            # photo is > 18 months old on a listing "just posted". Best-effort,
            # silent when the CDN strips EXIF (which is most of the time).
            try:
                age = await detect_photo_age(raw.photo_urls)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[{source}] photo-age detection failed: {exc}")
                age = None
            if age is not None and age.is_stale:
                reason = f"photos EXIF ≥ 18 mois ({age.newest_photo_date.date() if age.newest_photo_date else '?'})"
                scam.reasons.append(reason)
                scam.score = min(100, scam.score + 25)
                scam.is_scam = scam.score >= 50

            if scam.is_scam:
                listing.scam_score = scam.score
                listing.scam_reasons = scam.reasons
                listing.status = ListingStatus.scam
                await session.commit()
                stats["scam"] += 1
                logger.warning(
                    f"[{source}] 🚫 scam listing={raw.external_id} "
                    f"score={scam.score} reasons={scam.reasons[:2]}"
                )
                continue

            # Track scam_score even if below threshold — useful for tuning.
            if scam.score > 0:
                listing.scam_score = scam.score
                listing.scam_reasons = scam.reasons

            await session.commit()

            if created or price_changed:
                listing_ids_to_score.append(listing.id)
                stats["to_score"] += 1

    logger.info(
        f"[{source}] ✅ summary: seen={stats['seen']} new={stats['new_raw']} "
        f"out_of_zone={stats['out_of_zone']} cross_dup={stats['cross_site_dup']} "
        f"scam={stats['scam']} → to_score={stats['to_score']}"
    )

    if listing_ids_to_score:
        # Run scoring outside the scrape session to parallelise DB + Anthropic work.
        await asyncio.gather(
            *[_score_and_notify(listing_id, notifier) for listing_id in listing_ids_to_score],
            return_exceptions=True,
        )


async def _score_and_notify(listing_id: int, notifier: NotificationQueue) -> None:
    # === LEGACY ADMIN PATH (unchanged) ===
    # Scores with the env-configured Anthropic key + yaml criteria, alerts
    # go to settings.telegram_chat_ids (the admin's group). This path keeps
    # working unconditionally so existing alerts never break.
    factory = get_session_factory()
    async with factory() as session:
        pipeline = ScoringPipeline(session, get_criteria())
        try:
            scored = await pipeline.process_listing(listing_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Scoring pipeline failed for listing {listing_id}: {exc}")
            scored = None
        if scored is not None:
            try:
                await notifier.enqueue(scored)
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"Enqueue to notifier failed for listing {listing_id}: {exc}")
            # Auto-application (DossierFacile) — best-effort, never breaks alerts.
            try:
                from src.apply.auto_apply import run as _auto_apply
                from src.models.listing import Listing

                listing_obj = await session.get(Listing, listing_id)
                if listing_obj is not None:
                    await _auto_apply(session, scored, listing_obj, notifier)
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"Auto-apply failed for listing {listing_id}: {exc}")

    # === MULTI-TENANT PATH (gated by MULTITENANT_ENABLED) ===
    # Each active user scores with their OWN Anthropic key (BYOK) and
    # criteria, and gets their alerts in their OWN Telegram chat. Failures
    # are isolated per-user — never affect the legacy path or other users.
    if not get_settings().multitenant_enabled:
        return

    from sqlalchemy.orm import selectinload as _selectinload

    from src.models.user import User as _User
    from src.models.user import UserStatus as _UserStatus

    async with factory() as session:
        result = await session.execute(
            select(_User)
            .where(_User.status == _UserStatus.active)
            .options(_selectinload(_User.criteria))
        )
        active_users = list(result.scalars().all())

    for user in active_users:
        try:
            await _score_for_user(user, listing_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                f"Per-user scoring failed (user.id={user.id}, listing={listing_id}): {exc}"
            )


async def _score_for_user(user, listing_id: int) -> None:
    """Score one listing for one user with their BYOK + criteria.

    Pulled out of ``_score_and_notify`` so per-user failures can be caught
    cleanly and don't bleed onto siblings. Sends the alert (if any) directly
    to the user's ``alert_chat_id`` — bypasses the shared notifier queue.
    """
    from anthropic import AsyncAnthropic
    from cryptography.fernet import InvalidToken

    from src.config import SearchCriteria
    from src.notifier.telegram import TelegramNotifier
    from src.scoring.pipeline import ScoringPipeline
    from src.utils.crypto import decrypt

    if user.criteria is None or not user.anthropic_api_key_enc:
        logger.debug(f"user {user.id}: skipped (no criteria or no key)")
        return
    try:
        api_key = decrypt(user.anthropic_api_key_enc)
    except (InvalidToken, ValueError) as exc:
        logger.warning(f"user {user.id}: key decrypt failed ({exc})")
        return

    # Build the user's SearchCriteria from their stored JSON. The wizard saved
    # a shape compatible with SearchCriteria.model_validate.
    try:
        criteria = SearchCriteria.model_validate(user.criteria.criteria)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"user {user.id}: criteria JSON invalid ({exc})")
        return
    # Honour the user-chosen alert threshold (overrides the yaml default).
    criteria.scoring.alert_threshold = int(user.criteria.alert_threshold or 70)

    client = AsyncAnthropic(api_key=api_key)
    factory = get_session_factory()
    async with factory() as session:
        pipeline = ScoringPipeline(session, criteria, client=client)
        try:
            scored = await pipeline.process_listing(listing_id)
        except Exception as exc:  # noqa: BLE001
            # 401 / quota / etc — bump failure counter so we can disable
            # broken users after N strikes.
            user.consecutive_key_failures = (user.consecutive_key_failures or 0) + 1
            await session.merge(user)
            await session.commit()
            logger.warning(
                f"user {user.id}: scoring failed ({exc}); fails={user.consecutive_key_failures}"
            )
            return
        if scored is None:
            return

        # The chat_id where the user did /start IS the alert destination —
        # could be a DM or a shared group. One chat_id = one user record.
        per_user_notifier = TelegramNotifier(chat_id=user.telegram_chat_id)
        try:
            await per_user_notifier.send_listing(scored)
            user.alerts_sent_total = (user.alerts_sent_total or 0) + 1
            await session.merge(user)
            await session.commit()
            logger.info(
                f"🎯 user {user.id} ({user.telegram_username or 'anon'}): "
                f"alert sent (score={scored.score}, listing={listing_id})"
            )
        finally:
            await per_user_notifier.close()


async def send_heartbeat_job() -> None:
    """Daily 'I'm alive' ping on Telegram so you know the bot is still running
    even if it hasn't found a gem in a while.
    """
    from src.notifier.heartbeat import send_heartbeat

    factory = get_session_factory()
    async with factory() as session:
        try:
            await send_heartbeat(session)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"heartbeat failed: {exc}")


async def send_weekly_digest_job() -> None:
    from src.notifier.digest import send_weekly_digest

    factory = get_session_factory()
    async with factory() as session:
        try:
            await send_weekly_digest(session)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"weekly digest failed: {exc}")


async def rescore_price_drops_job() -> None:
    """Re-score listings with a recent price drop that haven't been re-evaluated yet.

    Safety net for cases where the previous scoring failed (budget cap,
    transient Anthropic error, etc). Walks the last 24h of price-drops and
    re-runs the pipeline on each listing not already re-scored in that window.
    """
    from src.models.listing import Listing as _Listing
    from src.models.listing import ListingStatus as _LStatus
    from src.models.price_history import PriceHistory as _PH
    from src.models.score import Score as _Score
    from src.models.score import ScoreStage as _Stage

    factory = get_session_factory()
    notifier = get_notification_queue()
    cutoff = datetime.now(UTC) - timedelta(hours=24)

    # Single session: fetch candidates, their statuses, AND their most recent
    # sonnet-score timestamp — all in 3 queries total, not 1 + 2*N.
    async with factory() as session:
        recent = await session.execute(
            select(_PH.listing_id)
            .where(_PH.observed_at >= cutoff)
            .group_by(_PH.listing_id)
            .having(func.count(_PH.id) >= 2)
        )
        listing_ids = [row[0] for row in recent.all()]
        if not listing_ids:
            logger.debug("no price-drop candidates in last 24h")
            return

        statuses = await session.execute(
            select(_Listing.id, _Listing.status).where(_Listing.id.in_(listing_ids))
        )
        status_map = dict(statuses.all())

        last_scores = await session.execute(
            select(_Score.listing_id, func.max(_Score.created_at))
            .where(_Score.listing_id.in_(listing_ids), _Score.stage == _Stage.sonnet_pepite)
            .group_by(_Score.listing_id)
        )
        last_score_map = dict(last_scores.all())

    skip_statuses = {_LStatus.filtered_out, _LStatus.scam, _LStatus.gone}
    to_rescore = [
        lid
        for lid in listing_ids
        if status_map.get(lid) not in skip_statuses
        and not (last_score_map.get(lid) and last_score_map[lid] >= cutoff)
    ]
    logger.info(f"rescore_price_drops: {len(listing_ids)} candidates, {len(to_rescore)} to rescore")

    for lid in to_rescore:
        try:
            await _score_and_notify(lid, notifier)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"rescore failed for listing {lid}: {exc}")


async def check_scraper_health() -> None:
    settings = get_settings()
    notifier = get_notification_queue()
    factory = get_session_factory()
    threshold = settings.scraper_fail_threshold

    async with factory() as session:
        result = await session.execute(select(ScraperHealth))
        rows = list(result.scalars().all())
        for row in rows:
            if row.consecutive_failures >= threshold and not row.alerted:
                await notifier.send_system(
                    f"🚨 Scraper *{row.source}* down "
                    f"({row.consecutive_failures} fails). Last error: `{(row.last_error or '')[:300]}`",
                    urgent=True,
                )
                row.alerted = True
                await session.commit()


async def check_budget_warnings() -> None:
    notifier = get_notification_queue()
    factory = get_session_factory()
    async with factory() as session:
        pipeline = ScoringPipeline(session, get_criteria())
        should_warn, spent, cap = await pipeline.budget_soft_warn()
        if should_warn:
            await notifier.send_system(
                f"⚠️ Claude budget at {spent:.2f}€ / {cap:.2f}€ "
                f"(**{(spent / cap) * 100:.0f}%**). Scoring will stop at 100%.",
                urgent=False,
            )


async def drift_detection_job() -> None:
    """Monthly tripwire: re-score a random sample of recent gems and alert if
    the new PROMPT_VERSION has shifted scores meaningfully.
    """
    from src.scoring.drift import detect_drift, format_drift_alert

    factory = get_session_factory()
    notifier = get_notification_queue()
    async with factory() as session:
        try:
            report = await detect_drift(session, get_criteria(), sample_size=10)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"drift detection failed: {exc}")
            return
    alert = format_drift_alert(report, threshold_pct=20.0)
    if alert:
        await notifier.send_system(alert, urgent=False)
    else:
        logger.info(
            f"drift OK: sample={report.sample_size}, "
            f"shifted≥10={report.shifted_by_10_or_more}, "
            f"shifted≥20={report.shifted_by_20_or_more}"
        )


async def cleanup_stale_listings_job(days_stale: int = 30) -> None:
    """Mark listings not seen in ``days_stale`` days as gone.

    Keeps the dashboard focused on what's still live and prevents
    "scam"/"filtered_out" listings from polluting long-term stats.
    """
    from sqlalchemy import update

    from src.models.listing import Listing as _Listing
    from src.models.listing import ListingStatus as _LStatus

    cutoff = datetime.now(UTC) - timedelta(days=days_stale)
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            update(_Listing)
            .where(_Listing.last_seen_at < cutoff, _Listing.status != _LStatus.gone)
            .values(status=_LStatus.gone)
            .returning(_Listing.id)
        )
        count = len(list(result.scalars().all()))
        await session.commit()
        if count:
            logger.info(f"Marked {count} stale listings as gone (>{days_stale}d unseen)")


def build_scheduler(criteria: SearchCriteria | None = None) -> AsyncIOScheduler:
    settings = get_settings()
    criteria = criteria or get_criteria()
    scheduler = AsyncIOScheduler(timezone=settings.tz)

    for source in SCRAPER_REGISTRY:
        interval = getattr(settings, SOURCE_INTERVALS_ATTR[source])
        jitter = max(5, interval // 3)
        scheduler.add_job(
            run_scraper,
            args=[source],
            trigger=IntervalTrigger(seconds=interval, jitter=jitter),
            id=f"scrape-{source}",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(UTC) + timedelta(seconds=random.uniform(1.0, 10.0)),
            misfire_grace_time=60,
        )

    scheduler.add_job(
        check_scraper_health,
        trigger=IntervalTrigger(minutes=5),
        id="health-check",
        coalesce=True,
    )
    scheduler.add_job(
        check_budget_warnings,
        trigger=IntervalTrigger(minutes=30),
        id="budget-check",
        coalesce=True,
    )

    # Scrapfly credit guard — every 5 min (account API is free), pauses DataDome
    # sources + alerts Telegram if the burn outruns a sustainable pace.
    if settings.scrapfly_guard_enabled and settings.scrapfly_api_key:
        from src.scrapers.scrapfly_guard import scrapfly_guard_job

        scheduler.add_job(
            scrapfly_guard_job,
            trigger=IntervalTrigger(minutes=5),
            id="scrapfly-guard",
            coalesce=True,
        )

    # Daily heartbeat — 09:00 Europe/Paris, so you know the bot is alive even
    # on a quiet day with no gems.
    scheduler.add_job(
        send_heartbeat_job,
        trigger=CronTrigger(hour=9, minute=0),
        id="daily-heartbeat",
        coalesce=True,
        misfire_grace_time=3600,
    )

    # Weekly digest — every Sunday 19:00 local time.
    scheduler.add_job(
        send_weekly_digest_job,
        trigger=CronTrigger(day_of_week="sun", hour=19, minute=0),
        id="weekly-digest",
        coalesce=True,
        misfire_grace_time=3600,
    )

    # Safety net: catch price drops that didn't trigger a re-score in real time.
    scheduler.add_job(
        rescore_price_drops_job,
        trigger=IntervalTrigger(hours=2),
        id="rescore-price-drops",
        coalesce=True,
    )

    # Daily cleanup — mark listings unseen for 30d as gone.
    scheduler.add_job(
        cleanup_stale_listings_job,
        trigger=CronTrigger(hour=4, minute=0),  # 04:00 local time
        id="cleanup-stale-listings",
        coalesce=True,
    )

    # Monthly drift detection: on the 1st at 05:00 local time, rescore 10 gems
    # and alert if the new PROMPT_VERSION shifted ≥ 20% of them by ≥ 10 points.
    scheduler.add_job(
        drift_detection_job,
        trigger=CronTrigger(day=1, hour=5, minute=0),
        id="drift-detection",
        coalesce=True,
        misfire_grace_time=3600,
    )

    return scheduler

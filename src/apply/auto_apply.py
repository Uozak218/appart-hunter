"""Auto-application: email a ready-to-go rental application on strong matches.

Flow (hooked into ``_score_and_notify`` right after the alert is enqueued):

    score >= AUTO_APPLY_MIN_SCORE  AND  a real landlord email is visible
    AND  not already applied        AND  under the daily cap
      -> Claude drafts a short FR message (``ContactDrafter``)
      -> DossierFacile link is appended
      -> LIVE: SMTP-send it + stamp ``applied_at`` + post a Telegram receipt
         DRY-RUN: post a "[TEST]" Telegram receipt only (nothing sent)

Only the EMAIL path is automatable. leboncoin/seloger go through their internal
messaging (no email) -> ``try_extract_email`` returns None -> we skip here and the
normal alert carries the drafted message for a manual 1-tap send.

Two switches drive the rollout:
  - ``auto_apply_enabled``  : feature on/off (drafts + receipts).
  - ``auto_apply_live``     : True = really send; False = dry-run (receipt only).

The decision is a pure function (``evaluate``) so it can be unit-tested without
DB / SMTP / Claude.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from src.config import get_settings
from src.models.listing import Listing
from src.notifier.email_sender import send_email, try_extract_email
from src.scoring.contact_draft import ContactDraft, ContactDrafter
from src.utils.logging import logger


@dataclass
class ApplyDecision:
    go: bool
    reason: str


def evaluate(
    *,
    enabled: bool,
    score: int,
    min_score: int,
    already_applied: bool,
    has_email: bool,
    applied_today: int,
    daily_cap: int,
) -> ApplyDecision:
    """Pure gate: should we (dry-run or live) act on this listing? No I/O."""
    if not enabled:
        return ApplyDecision(False, "auto-apply disabled")
    if score < min_score:
        return ApplyDecision(False, f"score {score} < min {min_score}")
    if already_applied:
        return ApplyDecision(False, "already applied")
    if not has_email:
        return ApplyDecision(False, "no landlord email (manual/assisted path)")
    if applied_today >= daily_cap:
        return ApplyDecision(False, f"daily cap {daily_cap} reached")
    return ApplyDecision(True, "ok")


def compose_body(message: str, dossier_url: str) -> str:
    """Append the DossierFacile link to the drafted message (if configured)."""
    msg = (message or "").strip()
    if dossier_url:
        return f"{msg}\n\nMon dossier complet (DossierFacile) : {dossier_url}"
    return msg


def _receipt(listing: Listing, email: str, draft: ContactDraft, body: str, *, live: bool) -> str:
    head = "✅ *Candidature ENVOYÉE*" if live else "🧪 *\\[TEST] Candidature prête (non envoyée)*"
    arr = f"Paris {listing.arrondissement}e" if listing.arrondissement else "Paris"
    rent = f"{int(listing.rent_cc)} €" if listing.rent_cc else "— €"
    to_line = "📧 envoyée à" if live else "📧 aurait été envoyée à"
    return (
        f"{head} — {arr}, {rent}\n"
        f"{to_line} : {email}\n"
        f"🔗 {listing.url}\n"
        f"✉️ Objet : {draft.subject}\n\n"
        f"{body}"
    )


async def _count_applied_last_24h(session) -> int:
    res = await session.execute(
        select(func.count())
        .select_from(Listing)
        .where(
            Listing.applied_at.isnot(None),
            Listing.applied_at > datetime.now(UTC) - timedelta(days=1),
        )
    )
    return int(res.scalar_one())


async def run(session, scored, listing: Listing, notifier) -> None:
    """Best-effort auto-application. Never raises into the caller."""
    s = get_settings()
    if not s.auto_apply_enabled:
        return

    email = try_extract_email(listing)
    applied_today = await _count_applied_last_24h(session) if email else 0
    decision = evaluate(
        enabled=s.auto_apply_enabled,
        score=scored.score,
        min_score=s.auto_apply_min_score,
        already_applied=listing.applied_at is not None,
        has_email=bool(email),
        applied_today=applied_today,
        daily_cap=s.auto_apply_daily_cap,
    )
    if not decision.go:
        logger.info(f"[auto-apply] listing {listing.id}: skip ({decision.reason})")
        return

    draft = await ContactDrafter(user_first_name=s.user_first_name).draft(listing)
    body = compose_body(draft.message, s.user_dossier_url)

    if not s.auto_apply_live:
        await notifier.send_system(_receipt(listing, email, draft, body, live=False))
        logger.info(f"[auto-apply] 🧪 dry-run listing {listing.id} (would send to {email})")
        return

    try:
        send_email(to=email, subject=draft.subject, body=body)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[auto-apply] send failed for listing {listing.id}: {exc}")
        await notifier.send_system(
            f"⚠️ Candidature NON envoyée (erreur SMTP) — {listing.url}\n{exc}"
        )
        return

    listing.applied_at = datetime.now(UTC)
    await session.commit()
    await notifier.send_system(_receipt(listing, email, draft, body, live=True))
    logger.info(f"[auto-apply] ✅ sent application for listing {listing.id} to {email}")

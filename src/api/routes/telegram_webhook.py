"""Telegram webhook endpoint.

Handles the ``contact:<listing_id>`` callback from the inline button we ship
with every alert. Drafts a FR contact message with Claude, then:
  - Sends the message back into the Telegram chat ready to copy-paste.
  - If ``AUTO_SEND_EMAIL=true`` AND we can detect a landlord email on the
    listing, also fires the email via SMTP using the configured Gmail/etc.
    account (see .env.example: SMTP_*).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config import get_settings
from src.models.listing import Listing, UserInterest
from src.notifier.email_sender import send_email, try_extract_email
from src.notifier.telegram import TelegramNotifier
from src.scoring.budget import BudgetManager
from src.scoring.contact_draft import ContactDrafter
from src.storage.database import get_session_factory
from src.utils.logging import logger

router = APIRouter(prefix="/telegram", tags=["telegram"])


_INTEREST_MAP = {
    "int": (UserInterest.interested, "❤️ Marqué comme intéressé"),
    "noint": (UserInterest.not_interested, "🚫 Marqué comme pas intéressé"),
    "done": (UserInterest.contacted, "📞 Marqué comme contacté"),
}


@router.post("/webhook")
async def telegram_webhook(request: Request) -> dict:
    payload = await request.json()
    logger.debug(f"telegram webhook payload keys: {list(payload.keys())}")

    callback = payload.get("callback_query")
    if callback:
        data = callback.get("data") or ""
        if data.startswith("contact:"):
            await _handle_contact_callback(int(data.split(":", 1)[1]))
        elif ":" in data:
            verb, raw_id = data.split(":", 1)
            if verb in _INTEREST_MAP and raw_id.isdigit():
                await _handle_interest_callback(int(raw_id), verb)
    return {"ok": True, "received": True}


async def _handle_interest_callback(listing_id: int, verb: str) -> None:
    interest, ack = _INTEREST_MAP[verb]
    notifier = TelegramNotifier()
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await session.execute(select(Listing).where(Listing.id == listing_id))
            listing = result.scalar_one_or_none()
            if listing is None:
                await notifier.send_text(f"Annonce `{listing_id}` introuvable.", silent=True)
                return
            listing.user_interest = interest
            await session.commit()
        await notifier.send_text(
            f"{ack} — {listing.title[:80]}",
            silent=True,
        )
    finally:
        await notifier.close()


async def _handle_contact_callback(listing_id: int) -> None:
    settings = get_settings()
    notifier = TelegramNotifier()
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await session.execute(
                select(Listing)
                .options(selectinload(Listing.photos))
                .where(Listing.id == listing_id)
            )
            listing = result.scalar_one_or_none()
            if listing is None:
                await notifier.send_text(f"Annonce `{listing_id}` introuvable.", silent=True)
                return

            budget = BudgetManager(session)
            if not await budget.allow_scoring():
                await notifier.send_text(
                    "⚠️ Budget mensuel Claude atteint — message non généré.", silent=True
                )
                return

            drafter = ContactDrafter(budget=budget, user_first_name=settings.user_first_name)
            try:
                draft = await drafter.draft(listing)
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"contact draft failed for {listing_id}: {exc}")
                await notifier.send_text(
                    f"❌ Erreur génération message pour `{listing_id}`.", silent=True
                )
                return
            await session.commit()

        email = try_extract_email(listing)
        sent = False
        if settings.auto_send_email and email:
            try:
                send_email(
                    to=email,
                    subject=draft.subject,
                    body=draft.message,
                )
                sent = True
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"email send failed for {listing.id}: {exc}")

        header = (
            f"✉️ *Envoyé à* `{email}`\n"
            if sent
            else "✍️ *Brouillon* (copier-coller)\n"
            + (
                f"📧 email détecté : `{email}`\n"
                if email
                else "📧 aucun email détecté — utiliser le formulaire de l'annonce\n"
            )
        )
        body = header + f"\n*Objet* : {draft.subject}\n" + "\n" + draft.message
        await notifier.send_text(body[:4000], silent=True)
    finally:
        await notifier.close()


def telegram_webhook_url() -> str | None:
    s = get_settings().telegram_webhook_url
    return s or None

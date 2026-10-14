"""Top-level bot command handlers (out of wizard scope).

``/start`` is owned by the wizard ConversationHandler. The commands below
are available once the user has finished onboarding:

- ``/criteres``  — show current criteria
- ``/modifier``  — restart the wizard (overwrites criteria)
- ``/pause``     — mute alerts
- ``/reprendre`` — re-enable alerts
- ``/stats``     — 7-day personal digest
- ``/cle``       — rotate the Anthropic API key
- ``/aide``      — list of commands
"""

from __future__ import annotations

from datetime import datetime, timedelta, UTC

from sqlalchemy import func, select
from telegram import Update
from telegram.ext import ContextTypes

from src.bot.repository import get_user_by_chat, save_anthropic_key, set_status
from src.models.user import UserStatus
from src.storage.database import get_session_factory


_HELP_TEXT = (
    "*Commandes disponibles*\n\n"
    "🚀 `/start` — premier lancement / wizard onboarding\n"
    "📋 `/criteres` — voir tes critères actuels\n"
    "✏️ `/modifier` — refaire le wizard (écrase les critères)\n"
    "⏸ `/pause` — couper les alertes\n"
    "▶️ `/reprendre` — re-activer les alertes\n"
    "📊 `/stats` — ton bilan 7 derniers jours\n"
    "🔑 `/cle <sk-ant-…>` — changer ta clé Anthropic\n"
    "❓ `/aide` — ce message"
)


async def _require_user(update: Update):
    """Helper: fetch the user or send a 'do /start first' message."""
    factory = get_session_factory()
    async with factory() as s:
        user = await get_user_by_chat(s, update.effective_chat.id)
    if user is None:
        await update.message.reply_text(
            "Tu n'es pas encore inscrit. Tape /start pour démarrer le wizard."
        )
        return None
    return user


async def cmd_criteres(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: ARG001
    factory = get_session_factory()
    async with factory() as s:
        user = await get_user_by_chat(s, update.effective_chat.id)
        if user is None:
            await update.message.reply_text("/start d'abord 🙂")
            return
        if user.criteria is None:
            await update.message.reply_text(
                "Pas encore de critères enregistrés. Tape /start ou /modifier."
            )
            return
        c = user.criteria.criteria or {}
        prop = c.get("property", {})
        zones = c.get("zones", {}).get("include", []) or []
        excludes = c.get("excludes", []) or []
        types = (prop.get("types") or {})
        types_str = ", ".join(types.get("preferred", []) + types.get("acceptable", []))
        arrs = ", ".join(z.get("code", "?")[-2:] for z in zones)
        msg = (
            f"📋 *Tes critères*\n\n"
            f"• Statut : {user.status.value}\n"
            f"• Budget max : {prop.get('max_rent_cc', '?')}€ CC\n"
            f"• Surface min : "
            f"{min((prop.get('min_surface') or {'_':0}).values(), default=0)} m²\n"
            f"• Types : {types_str}\n"
            f"• Arrondissements : {arrs}\n"
            f"• Exclus : {', '.join(excludes) or '(aucun)'}\n"
            f"• Seuil alerte : {user.criteria.alert_threshold}/100\n\n"
            f"_Pour modifier, tape /modifier (relance le wizard)._"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: ARG001
    user = await _require_user(update)
    if user is None:
        return
    factory = get_session_factory()
    async with factory() as s:
        u = await get_user_by_chat(s, update.effective_chat.id)
        await set_status(s, u, UserStatus.paused)
        await s.commit()
    await update.message.reply_text("⏸ Alertes coupées. /reprendre pour les ré-activer.")


async def cmd_reprendre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: ARG001
    user = await _require_user(update)
    if user is None:
        return
    factory = get_session_factory()
    async with factory() as s:
        u = await get_user_by_chat(s, update.effective_chat.id)
        await set_status(s, u, UserStatus.active)
        await s.commit()
    await update.message.reply_text("▶️ Alertes ré-activées. Bonne chasse 🐾")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: ARG001
    user = await _require_user(update)
    if user is None:
        return
    # Simple stats for now — alerts_sent_total + last_active.
    days7 = datetime.now(UTC) - timedelta(days=7)
    await update.message.reply_text(
        f"📊 *Tes stats*\n\n"
        f"• Alertes envoyées (total) : {user.alerts_sent_total}\n"
        f"• Dernière activité : {user.last_active_at.strftime('%Y-%m-%d %H:%M')}\n"
        f"• Statut : {user.status.value}\n\n"
        f"_Heartbeat 24h global envoyé chaque matin à 9h._",
        parse_mode="Markdown",
    )


async def cmd_cle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/cle sk-ant-...`` — rotate the Anthropic API key."""
    user = await _require_user(update)
    if user is None:
        return
    args = context.args or []
    if not args or not args[0].startswith("sk-ant-"):
        await update.message.reply_text(
            "Usage: `/cle sk-ant-...`",
            parse_mode="Markdown",
        )
        return
    key = args[0]
    # Quick API probe.
    from src.bot.wizard import _validate_anthropic_key

    if not await _validate_anthropic_key(key):
        await update.message.reply_text("❌ Clé refusée par Anthropic. Vérifie et réessaie.")
        return
    factory = get_session_factory()
    async with factory() as s:
        u = await get_user_by_chat(s, update.effective_chat.id)
        await save_anthropic_key(s, u, key)
        await s.commit()
    await update.message.reply_text("✅ Clé rotation OK.")


async def cmd_modifier(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias to restart the wizard."""
    from src.bot.wizard import start_wizard

    await start_wizard(update, context)


async def cmd_aide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: ARG001
    await update.message.reply_text(_HELP_TEXT, parse_mode="Markdown")

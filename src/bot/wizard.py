"""Onboarding wizard — 8-question ConversationHandler.

Flow (state machine):
    ASK_KEY → VALIDATE_KEY → ASK_BUDGET → ASK_SURFACE → ASK_TYPES →
    ASK_ARRS → ASK_EXCLUDES → ASK_THRESHOLD → CONFIRM → done

State per chat is held both in ``context.user_data`` (Telegram in-mem) and
persisted to ``users.wizard_step`` (DB) so an interrupted wizard can be
resumed (we ping the user to keep going on next ``/start``).
"""

from __future__ import annotations

import re

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src.bot.repository import (
    get_or_create_user,
    get_user_by_chat,
    save_anthropic_key,
    save_criteria,
    set_wizard_step,
)
from src.models.user import WizardStep
from src.storage.database import get_session_factory
from src.utils.logging import logger

# ConversationHandler states (must be integers / unique).
(
    S_ASK_KEY,
    S_ASK_BUDGET,
    S_ASK_SURFACE,
    S_ASK_TYPES,
    S_ASK_ARRS,
    S_ASK_EXCLUDES,
    S_ASK_THRESHOLD,
    S_CONFIRM,
) = range(8)

# Defaults match the existing search_criteria.yaml so a fresh user gets a
# sensible baseline they can override at each step.
DEFAULT_BUDGET = 2000
DEFAULT_SURFACE = 40
DEFAULT_EXCLUDES = "meuble, colocation, sous-location, courte-duree"
DEFAULT_THRESHOLD = 70


def _criteria_template(
    budget: int, surface: int, types: list[str], arrs: list[str], excludes: list[str]
) -> dict:
    """Build the criteria dict matching ``config.SearchCriteria`` shape."""
    min_surface = {t: float(surface) for t in types}
    return {
        "property": {
            "types": {
                "preferred": ["T3"] if "T3" in types else types[:1],
                "acceptable": [t for t in types if t != ("T3" if "T3" in types else types[0])],
            },
            "min_surface": min_surface,
            "max_rent_cc": float(budget),
            "furnished": False,
            "floor": {"no_ground_floor": True, "elevator_required_above": 4},
        },
        "zones": {
            "include": [{"code": f"75{int(a):03d}"} for a in arrs],
            "line14_proximity": {"enabled": True, "max_walk_minutes": 5.0},
        },
        "excludes": [e.strip().lower() for e in excludes if e.strip()],
    }


async def _validate_anthropic_key(key: str) -> bool:
    """Cheap test call to confirm the key works. Returns True on 200."""
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            # /v1/models is a free, low-latency probe.
            r = await c.get("https://api.anthropic.com/v1/models", headers=headers)
            return r.status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"anthropic probe failed: {exc}")
            return False


# ------------------------------------------------------------------ start


async def start_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat = update.effective_chat
    user_tg = update.effective_user
    factory = get_session_factory()
    async with factory() as s:
        user, created = await get_or_create_user(
            s,
            chat.id,
            user_tg.username if user_tg else None,
            user_tg.first_name if user_tg else None,
        )
        await set_wizard_step(s, user, WizardStep.ask_key)
        await s.commit()

    context.user_data.clear()
    greeting = "👋 Bienvenue !" if created else f"👋 Rebonjour {user_tg.first_name or ''}!"
    await update.message.reply_text(
        f"{greeting} Je suis *Appart Hunter* — je scan ~20 sites immobiliers parisiens "
        "et je te ping quand une *vraie pépite* sort.\n\n"
        "Tu vas répondre à 8 questions (~2 min). Tu peux taper `/annuler` à tout moment.\n\n"
        "*1/8 — Ta clé API Anthropic* (commence par `sk-ant-...`)\n"
        "Crée-en une sur https://console.anthropic.com/settings/keys (compte gratuit, "
        "tu paies juste les annonces scorées, ~5-15€/mois selon volume).",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )
    return S_ASK_KEY


# ----------------------------------------------------------------- ask_key


async def handle_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if not text.startswith("sk-ant-"):
        await update.message.reply_text(
            "🤔 Ta clé doit commencer par `sk-ant-`. Réessaie ?",
            parse_mode="Markdown",
        )
        return S_ASK_KEY

    msg = await update.message.reply_text("⏳ Je teste ta clé…")
    if not await _validate_anthropic_key(text):
        await msg.edit_text(
            "❌ Cette clé n'a pas l'air valide (l'API Anthropic la refuse). Réessaie ?"
        )
        return S_ASK_KEY

    factory = get_session_factory()
    async with factory() as s:
        user = await get_user_by_chat(s, update.effective_chat.id)
        await save_anthropic_key(s, user, text)
        await set_wizard_step(s, user, WizardStep.ask_budget)
        await s.commit()

    await msg.edit_text("✅ Clé OK, chiffrée et stockée.")
    await update.message.reply_text(
        f"*2/8 — Budget max CC (charges comprises) en €/mois ?* (défaut {DEFAULT_BUDGET})",
        parse_mode="Markdown",
    )
    return S_ASK_BUDGET


# ----------------------------------------------------------- ask_budget


async def handle_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    try:
        budget = int(re.sub(r"[^\d]", "", text) or DEFAULT_BUDGET)
    except ValueError:
        await update.message.reply_text("Donne un nombre, e.g. `2000`")
        return S_ASK_BUDGET
    if budget < 300 or budget > 10000:
        await update.message.reply_text("Hors plage raisonnable (300–10000). Réessaie ?")
        return S_ASK_BUDGET
    context.user_data["budget"] = budget
    await update.message.reply_text(
        f"💰 Budget = {budget}€\n\n*3/8 — Surface minimum en m² ?* (défaut {DEFAULT_SURFACE})",
        parse_mode="Markdown",
    )
    return S_ASK_SURFACE


# ---------------------------------------------------------- ask_surface


async def handle_surface(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    try:
        surface = int(re.sub(r"[^\d]", "", text) or DEFAULT_SURFACE)
    except ValueError:
        await update.message.reply_text("Donne un nombre, e.g. `40`")
        return S_ASK_SURFACE
    if surface < 10 or surface > 200:
        await update.message.reply_text("Hors plage 10–200. Réessaie ?")
        return S_ASK_SURFACE
    context.user_data["surface"] = surface

    keyboard = [
        [
            InlineKeyboardButton("T2 uniquement", callback_data="types:T2"),
            InlineKeyboardButton("T3 uniquement", callback_data="types:T3"),
        ],
        [InlineKeyboardButton("T2 + T3 (recommandé)", callback_data="types:T2,T3")],
    ]
    await update.message.reply_text(
        f"📐 Surface min = {surface}m²\n\n*4/8 — Types de bien ?*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return S_ASK_TYPES


# ----------------------------------------------------------- ask_types


async def handle_types(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    payload = q.data.split(":", 1)[1]
    types = [t.strip() for t in payload.split(",") if t.strip()]
    context.user_data["types"] = types
    await q.edit_message_text(f"🏠 Types = {', '.join(types)}")
    await q.message.reply_text(
        "*5/8 — Arrondissements ciblés ?*\n\n"
        "Tape les numéros séparés par espace ou virgule.\n"
        "Exemple: `3 4 9 10 11 12 13 14 18`\n\n"
        "_Tu peux aussi écrire `tous` = 75001 → 75020._",
        parse_mode="Markdown",
    )
    return S_ASK_ARRS


# ----------------------------------------------------------- ask_arrs


async def handle_arrs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip().lower()
    if text in ("tous", "all"):
        arrs = [str(i) for i in range(1, 21)]
    else:
        # Accept space / comma / mixed.
        raw = re.split(r"[\s,]+", text)
        arrs = []
        for r in raw:
            try:
                n = int(r)
                if 1 <= n <= 20:
                    arrs.append(str(n))
            except ValueError:
                continue
    if not arrs:
        await update.message.reply_text(
            "Aucun arrondissement valide trouvé. Donne des numéros 1-20, e.g. `11 12 13`"
        )
        return S_ASK_ARRS
    context.user_data["arrs"] = arrs
    await update.message.reply_text(
        f"🗺️ Arr = {', '.join(arrs)}\n\n"
        f"*6/8 — Mots-clés à exclure ?*\n\n"
        f"Annonces contenant ces mots seront rejetées. Défaut :\n"
        f"`{DEFAULT_EXCLUDES}`\n\n"
        f"Tape `ok` pour garder le défaut, sinon donne ta liste séparée par virgules.",
        parse_mode="Markdown",
    )
    return S_ASK_EXCLUDES


# -------------------------------------------------------- ask_excludes


async def handle_excludes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    excludes_str = DEFAULT_EXCLUDES if text.lower() in ("ok", "défaut", "default") else text
    excludes = [e.strip().lower() for e in excludes_str.split(",") if e.strip()]
    context.user_data["excludes"] = excludes
    await update.message.reply_text(
        f"🚫 Exclus = {', '.join(excludes) or '(aucun)'}\n\n"
        f"*7/8 — Seuil d'alerte ?* (0-100, défaut {DEFAULT_THRESHOLD})\n\n"
        f"Une annonce te ping si Claude lui donne ≥ ce score.\n"
        f"70 = pépite. 60 = annonce correcte. 80 = vraie rareté.",
        parse_mode="Markdown",
    )
    return S_ASK_THRESHOLD


# --------------------------------------------------------- ask_threshold


async def handle_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    try:
        th = int(re.sub(r"[^\d]", "", text) or DEFAULT_THRESHOLD)
    except ValueError:
        await update.message.reply_text("Donne un nombre 0-100, e.g. `70`")
        return S_ASK_THRESHOLD
    if th < 0 or th > 100:
        await update.message.reply_text("0–100 stp")
        return S_ASK_THRESHOLD
    context.user_data["threshold"] = th

    d = context.user_data
    recap = (
        f"📋 *Récap*\n\n"
        f"• Budget : {d['budget']}€ CC\n"
        f"• Surface min : {d['surface']} m²\n"
        f"• Types : {', '.join(d['types'])}\n"
        f"• Arr : {', '.join(d['arrs'])}\n"
        f"• Exclus : {', '.join(d['excludes']) or '(aucun)'}\n"
        f"• Seuil alerte : {th}/100\n\n"
        f"*8/8 — On enregistre ?*"
    )
    keyboard = [
        [
            InlineKeyboardButton("✅ Oui, go !", callback_data="confirm:yes"),
            InlineKeyboardButton("❌ Annuler", callback_data="confirm:no"),
        ]
    ]
    await update.message.reply_text(
        recap, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return S_CONFIRM


# ------------------------------------------------------------ confirm


async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data.endswith(":no"):
        await q.edit_message_text("Annulé. Tape /start pour reprendre.")
        return ConversationHandler.END

    d = context.user_data
    criteria = _criteria_template(
        budget=d["budget"],
        surface=d["surface"],
        types=d["types"],
        arrs=d["arrs"],
        excludes=d["excludes"],
    )
    factory = get_session_factory()
    async with factory() as s:
        user = await get_user_by_chat(s, update.effective_chat.id)
        await save_criteria(s, user, criteria, alert_threshold=d["threshold"])
        await s.commit()
        uid = user.id

    await q.edit_message_text(
        f"🎉 *Enregistré !* (user #{uid})\n\n"
        "Je te ping ici dès qu'une pépite sort.\n"
        "`/criteres` pour voir tes critères · `/pause` pour suspendre · "
        "`/stats` pour ton bilan · `/aide` pour la liste complète.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# -------------------------------------------------------------- cancel


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Annulé. Tape /start pour recommencer.")
    return ConversationHandler.END


def build_wizard() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("start", start_wizard)],
        states={
            S_ASK_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_key)],
            S_ASK_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_budget)],
            S_ASK_SURFACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_surface)],
            S_ASK_TYPES: [CallbackQueryHandler(handle_types, pattern=r"^types:")],
            S_ASK_ARRS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_arrs)],
            S_ASK_EXCLUDES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_excludes)],
            S_ASK_THRESHOLD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_threshold)],
            S_CONFIRM: [CallbackQueryHandler(handle_confirm, pattern=r"^confirm:")],
        },
        fallbacks=[CommandHandler("annuler", cancel), CommandHandler("cancel", cancel)],
        # Drop the conversation if the user goes silent for 30 min.
        conversation_timeout=1800,
    )

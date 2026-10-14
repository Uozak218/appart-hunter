"""Conversational layer — ``/demande <question>`` parle au bot via Claude.

L'utilisateur ou sa partenaire posent une question dans le groupe avec la
commande ``/demande``. Le bot :

1. Récupère le contexte 24h (annonces vues, scorées, pépites, top 3).
2. Envoie le tout à Claude Haiku 4.5 avec un system prompt cacheable.
3. Répond dans le groupe (Telegram natif, tout le monde voit).

Coût : ~0.1-0.5¢ par question. Cap : 1 réponse / 10 sec par chat (anti-loop).

NB : on utilise une commande slash plutôt qu'un prefix-match texte pour ne
pas avoir à désactiver Privacy Mode dans BotFather. Telegram livre toujours
les commandes au bot, même en privacy ON.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from anthropic import AsyncAnthropic
from sqlalchemy import desc, func, select
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from src.config import get_settings
from src.models.listing import Listing
from src.models.score import Score, ScoreStage
from src.storage.database import get_session_factory
from src.utils.logging import logger

# Per-chat throttling — empêche de brûler des tokens si quelqu'un spam.
_LAST_REPLY_AT: dict[int, float] = {}
_MIN_INTERVAL_S = 10.0

SYSTEM_PROMPT = """Tu es l'assistant conversationnel de Appart Hunter, un bot de
chasse aux pépites immobilières parisiennes. Tu réponds aux questions de
l'utilisateur et sa partenaire dans leur groupe Telegram privé.

Tes capacités :
- Tu reçois dans chaque question un contexte avec :
  * Les totaux DEPUIS LE DÉBUT du bot (annonces vues, scorées, pépites détectées)
  * Les totaux des dernières 24h
  * La liste des sources actives (sites scrapés)
  * Le top 5 des pépites jamais détectées
- Tu peux donc répondre aussi bien à "que s'est-il passé aujourd'hui ?" qu'à
  "quelle est ma meilleure pépite depuis le début ?" ou "combien d'annonces
  on a déjà vues au total ?".
- Tu peux suggérer des ajustements de critères si l'utilisateur trouve qu'il
  reçoit trop ou pas assez d'alertes.
- Tu réponds toujours en français, ton chaleureux et bref (2-4 phrases max
  sauf si l'utilisateur demande une analyse détaillée).
- Tu ne sais PAS scraper en temps réel ni déclencher des actions — tu peux
  juste répondre / expliquer / suggérer.

Tu N'es PAS un agent immobilier — tu n'inventes JAMAIS d'annonces qui n'ont
pas été scrapées. Si tu n'as pas la donnée, dis-le franchement.
"""


async def _stats_block(s, *, since=None, label: str) -> str:  # noqa: ANN001
    """Sub-block: counts + top 3 listings. ``since=None`` → all-time."""
    base_listing = select(func.count(Listing.id))
    base_scored = select(func.count(Score.id)).where(
        Score.stage == ScoreStage.sonnet_pepite, Score.score.isnot(None)
    )
    base_alerts = select(func.count(Score.id)).where(
        Score.stage == ScoreStage.sonnet_pepite, Score.score >= 70
    )
    if since is not None:
        base_listing = base_listing.where(Listing.first_seen_at >= since)
        base_scored = base_scored.where(Score.created_at >= since)
        base_alerts = base_alerts.where(Score.created_at >= since)

    seen = (await s.execute(base_listing)).scalar_one()
    scored = (await s.execute(base_scored)).scalar_one()
    alerts = (await s.execute(base_alerts)).scalar_one()
    return f"  {label} → vues={seen}, scorées={scored}, pépites≥70={alerts}"


async def _build_context() -> str:
    """Provide Claude with full history + 24h breakdown + top 5 ever."""
    f = get_session_factory()
    since_24h = datetime.now(UTC) - timedelta(hours=24)
    async with f() as s:
        all_time = await _stats_block(s, since=None, label="depuis le début")
        last_24h = await _stats_block(s, since=since_24h, label="dernières 24h")

        # Sources actives
        from src.scrapers import SCRAPER_REGISTRY

        sources = sorted(SCRAPER_REGISTRY.keys())

        # Top 5 all-time pépites
        top = (
            await s.execute(
                select(
                    Score.score,
                    Listing.title,
                    Listing.postal_code,
                    Listing.rent_cc,
                    Listing.url,
                    Listing.surface_m2,
                    Score.created_at,
                )
                .join(Listing, Listing.id == Score.listing_id)
                .where(
                    Score.stage == ScoreStage.sonnet_pepite,
                    Score.score.isnot(None),
                )
                .order_by(desc(Score.score), desc(Score.created_at))
                .limit(5)
            )
        ).all()

    top_str = (
        "\n".join(
            f"    {sc}/100 [{ts.strftime('%m-%d')}] {pc} — {surf:.0f}m² — {rent:.0f}€ — "
            f"{(t or '')[:55]} ({u})"
            for sc, t, pc, rent, u, surf, ts in top
        )
        or "    (aucune annonce scorée par Sonnet pour l'instant)"
    )
    return (
        "Activité de Appart Hunter:\n"
        f"{all_time}\n"
        f"{last_24h}\n"
        f"\n  Sources actives ({len(sources)}) : {', '.join(sources)}\n"
        f"\n  Top 5 pépites depuis le début :\n{top_str}"
    )


async def cmd_demande(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/demande <ta question>`` — répond via Claude avec contexte 24h."""
    msg = update.message
    if not msg:
        return
    question = " ".join(context.args or []).strip()
    if not question:
        await msg.reply_text(
            "Usage : `/demande ta question`\n\n"
            "Exemples :\n"
            "  • `/demande pourquoi je reçois si peu d'alertes ?`\n"
            "  • `/demande c'est quoi la pépite la mieux scorée aujourd'hui ?`\n"
            "  • `/demande conseille-moi pour élargir mes critères`",
            parse_mode="Markdown",
        )
        return

    chat_id = msg.chat_id
    now = time.monotonic()
    last = _LAST_REPLY_AT.get(chat_id, 0)
    if now - last < _MIN_INTERVAL_S:
        await msg.reply_text(
            f"⏳ Tranquille, j'ai répondu il y a moins de {_MIN_INTERVAL_S:.0f}s. Réessaie."
        )
        return
    _LAST_REPLY_AT[chat_id] = now

    settings = get_settings()
    if not settings.anthropic_api_key:
        await msg.reply_text("Pas de clé Anthropic configurée côté bot 😕")
        return

    ctx_block = await _build_context()
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        await context.bot.send_chat_action(chat_id, action="typing")
        resp = await client.messages.create(
            model=settings.anthropic_haiku_model,
            max_tokens=600,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": f"{ctx_block}\n\nQuestion: {question}"}],
        )
        answer = (
            "".join(
                getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
            ).strip()
            or "(pas de réponse)"
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"/demande failed: {exc}")
        await msg.reply_text(f"🥲 Erreur côté Claude : `{str(exc)[:120]}`", parse_mode="Markdown")
        return

    if len(answer) > 3500:
        answer = answer[:3500] + "…"
    await msg.reply_text(answer)


def build_chat_handlers() -> list[CommandHandler]:
    """Registers `/demande` (+ alias `/ask` pour les anglophones)."""
    return [
        CommandHandler("demande", cmd_demande),
        CommandHandler("ask", cmd_demande),
    ]

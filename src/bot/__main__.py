"""``python -m src.bot`` — Telegram bot entrypoint (long-polling).

Runs in its own container alongside the scraper. Reuses the same
``TELEGRAM_BOT_TOKEN`` (the bot user is shared with the alerts emitter —
incoming DMs from users land here, outgoing alerts are still fired from
the scraper's ``TelegramNotifier``).
"""

from __future__ import annotations

import logging

from telegram.ext import ApplicationBuilder, CommandHandler

from src.bot.chat import build_chat_handlers
from src.bot.handlers import (
    cmd_aide,
    cmd_cle,
    cmd_criteres,
    cmd_modifier,
    cmd_pause,
    cmd_reprendre,
    cmd_stats,
)
from src.bot.wizard import build_wizard
from src.config import get_settings
from src.utils.logging import logger, setup_logging


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing — set it in .env")

    # Quiet down python-telegram-bot's verbose internals.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)

    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    # Wizard (owns /start) — ConversationHandler takes priority.
    app.add_handler(build_wizard())

    # Post-onboarding commands.
    app.add_handler(CommandHandler("criteres", cmd_criteres))
    app.add_handler(CommandHandler("modifier", cmd_modifier))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("reprendre", cmd_reprendre))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("cle", cmd_cle))
    app.add_handler(CommandHandler("aide", cmd_aide))
    app.add_handler(CommandHandler("help", cmd_aide))

    # /demande <question> — conversational Claude with 24h context.
    for h in build_chat_handlers():
        app.add_handler(h)

    logger.info("🤖 Telegram bot starting (long-polling)…")
    # ``run_polling`` blocks. drop_pending_updates avoids replaying a backlog
    # of /start commands that happened while the bot was down.
    app.run_polling(drop_pending_updates=True, close_loop=True)


if __name__ == "__main__":
    main()

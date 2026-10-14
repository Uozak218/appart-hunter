"""Telegram bot module — multi-tenant onboarding + commands.

Runs as a separate container (``docker compose ... bot``) using long-polling
against the same ``TELEGRAM_BOT_TOKEN`` as the alerts emitter. New users DM
the bot, go through an 8-question wizard, and end up with a row in ``users``
+ ``user_criteria`` that the scoring pipeline routes alerts against.
"""

"""Entrypoint: runs scheduler + FastAPI side by side in a single process.

For Docker Compose we typically split these: `api` runs uvicorn on
``src.api.app:app`` and `scraper` runs ``python -m src.main scheduler``.
Kept together here for local dev convenience.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys

import uvicorn

from src.api.app import create_app
from src.config import get_criteria, get_settings
from src.notifier.queue import get_notification_queue
from src.scheduler.jobs import build_scheduler
from src.storage.database import dispose_engine
from src.utils.logging import logger, setup_logging


async def _run_scheduler() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info(f"Starting scheduler in env={settings.app_env}")

    criteria = get_criteria()
    scheduler = build_scheduler(criteria)
    notifier = get_notification_queue()
    await notifier.start()

    scheduler.start()
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:  # pragma: no cover - Windows fallback
            signal.signal(sig, lambda *_: _handle_signal())

    try:
        await stop_event.wait()
    finally:
        scheduler.shutdown(wait=False)
        await notifier.stop()
        await dispose_engine()
        logger.info("Scheduler stopped cleanly")


def _run_api() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    uvicorn.run(
        "src.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        factory=False,
    )


async def _run_all() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    config = uvicorn.Config(
        create_app(),
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    api_task = asyncio.create_task(server.serve(), name="uvicorn")
    scheduler_task = asyncio.create_task(_run_scheduler(), name="scheduler")

    done, pending = await asyncio.wait(
        [api_task, scheduler_task], return_when=asyncio.FIRST_COMPLETED
    )
    for t in pending:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t


def main() -> None:
    parser = argparse.ArgumentParser("pfh")
    parser.add_argument(
        "mode",
        nargs="?",
        default="all",
        choices=["all", "scheduler", "api", "selftest", "digest", "heartbeat"],
        help="Which process(es) to run (default: all, for local dev).",
    )
    args = parser.parse_args()

    try:
        if args.mode == "api":
            _run_api()
        elif args.mode == "scheduler":
            asyncio.run(_run_scheduler())
        elif args.mode == "selftest":
            from src.selftest import run as selftest_run

            sys.exit(asyncio.run(selftest_run()))
        elif args.mode == "digest":
            from src.scheduler.jobs import send_weekly_digest_job

            asyncio.run(send_weekly_digest_job())
        elif args.mode == "heartbeat":
            from src.scheduler.jobs import send_heartbeat_job

            asyncio.run(send_heartbeat_job())
        else:
            asyncio.run(_run_all())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()

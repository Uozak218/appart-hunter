"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.routes import admin, dashboard, health, metrics, telegram_webhook
from src.config import get_settings
from src.notifier.queue import get_notification_queue
from src.storage.database import dispose_engine
from src.utils.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    queue = get_notification_queue()
    await queue.start()
    try:
        yield
    finally:
        await queue.stop()
        await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Paris Flat Hunter",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(dashboard.router)
    app.include_router(metrics.router)
    app.include_router(telegram_webhook.router)
    return app


app = create_app()

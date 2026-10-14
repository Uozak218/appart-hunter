"""Playwright context setup with stealth, UA rotation, and persistent cookies.

Stealth is critical for LeBonCoin (DataDome) and SeLoger (Cloudflare).
We persist the browser context per-source under ``.playwright/<source>`` so
cookies and local storage survive restarts (helps pass bot detection).
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, async_playwright
from playwright_stealth import Stealth

from src.config import get_settings
from src.scrapers.proxies import build_proxy_option
from src.utils.logging import logger

_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
_BLOCKED_URL_HINTS = (
    "google-analytics",
    "googletagmanager",
    "doubleclick",
    "facebook.net",
    "hotjar",
    "criteo",
    "adsrvr",
    "segment.io",
    "sentry.io",
    "datadog",
    "amplitude",
    "intercom",
    "optimizely",
)


async def _block_heavy_resources(route, request) -> None:
    if request.resource_type in _BLOCKED_RESOURCE_TYPES:
        await route.abort()
        return
    if any(hint in request.url for hint in _BLOCKED_URL_HINTS):
        await route.abort()
        return
    await route.continue_()


USER_AGENTS = [
    # Rotating realistic UAs — keep small set, matching our accept-language.
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


def pick_user_agent() -> str:
    return random.choice(USER_AGENTS)


def _storage_path(source: str) -> Path:
    p = Path(".playwright") / source
    p.mkdir(parents=True, exist_ok=True)
    return p


class StealthBrowser:
    """Async context manager yielding a stealth-configured persistent context."""

    def __init__(self, source: str, viewport: tuple[int, int] = (1280, 900)) -> None:
        self.source = source
        self.viewport = viewport
        self._pw: Any = None
        self.context: BrowserContext | None = None
        self._stealth = Stealth(navigator_languages_override=("fr-FR", "fr"))

    async def __aenter__(self) -> BrowserContext:
        settings = get_settings()
        self._pw = await async_playwright().start()
        user_data_dir = _storage_path(self.source)
        user_agent = pick_user_agent()
        proxy = build_proxy_option()

        logger.debug(
            f"Launching stealth browser for {self.source} (headless={settings.playwright_headless}, "
            f"proxy={'yes' if proxy else 'no'})"
        )
        self.context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=settings.playwright_headless,
            viewport={"width": self.viewport[0], "height": self.viewport[1]},
            user_agent=user_agent,
            locale="fr-FR",
            timezone_id=settings.tz,
            extra_http_headers={
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
            },
            proxy=proxy,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        # Apply stealth to every new page.
        self.context.on("page", lambda p: self._apply_stealth(p))
        # Block bandwidth-heavy + useless-for-scraping resources. Massive win when
        # routing through a rate-limited residential proxy — also cheaper ($/GB).
        await self.context.route("**/*", _block_heavy_resources)
        return self.context

    async def _apply_stealth(self, page) -> None:
        try:
            await self._stealth.apply_stealth_async(page)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"stealth patch failed: {exc}")

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if self.context is not None:
                await self.context.close()
        finally:
            if self._pw is not None:
                await self._pw.stop()

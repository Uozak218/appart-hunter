"""Residential proxy plumbing.

Disabled by default — toggle with ``USE_PROXIES=true`` in .env and set
``PROXY_URL`` (or the provider-specific vars below). Keep this tiny and
provider-agnostic so we can swap IPRoyal / Webshare without code changes.
"""

from __future__ import annotations

from urllib.parse import urlparse

from src.config import get_settings
from src.utils.logging import logger


def build_proxy_option() -> dict | None:
    """Return a Playwright-compatible proxy dict or None."""
    settings = get_settings()
    if not settings.use_proxies:
        return None
    if not settings.proxy_url:
        logger.warning("USE_PROXIES is true but PROXY_URL is empty — running without a proxy")
        return None

    parsed = urlparse(settings.proxy_url)
    if not parsed.scheme or not parsed.hostname:
        logger.warning(f"Invalid PROXY_URL: {settings.proxy_url}")
        return None

    server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 8080}"
    option: dict = {"server": server}
    if parsed.username:
        option["username"] = parsed.username
    if parsed.password:
        option["password"] = parsed.password
    logger.info(f"Using proxy {server} (provider={settings.proxy_provider or 'custom'})")
    return option


def get_httpx_proxy() -> str | None:
    """Return the full proxy URL (with creds) for httpx.AsyncClient(proxy=...), or None."""
    settings = get_settings()
    if not settings.use_proxies or not settings.proxy_url:
        return None
    return settings.proxy_url

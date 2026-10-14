"""curl_cffi async client — Chrome TLS impersonation + iproyal residential proxy.

Used by sources whose anti-bot stack rejects both ``httpx`` and Scrapfly
(empirically observed on Foncia 2026-05-19: TLS+IP fingerprinting drop
non-browser requests silently). ``curl_cffi`` reuses Chrome's actual TLS
handshake, and routing through iproyal swaps the datacenter IP for a
French residential one — together they pass.

Two helpers:

- :func:`fetch_html` — get a URL, return the decoded text body.
- :func:`fetch_bytes` — get a URL, return raw bytes (used for photos).

The proxy is honored only when ``settings.use_proxies`` is True *and* a
``settings.proxy_url`` is configured. Foncia's anti-bot (observed 2026-06-02)
is satisfied by the Chrome TLS impersonation alone — the residential IP is no
longer required — so with ``use_proxies=False`` the client goes direct.
Flip ``USE_PROXIES=true`` again if a source ever needs the residential IP.
"""

from __future__ import annotations

import asyncio
from typing import Any

from curl_cffi.requests import AsyncSession

from src.config import get_settings
from src.utils.logging import logger


_DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}
_BROWSER_IMPERSONATION = "chrome120"


def _proxies() -> dict[str, str] | None:
    s = get_settings()
    # Honor the global ``use_proxies`` flag: foncia works direct via Chrome TLS
    # impersonation, so we only proxy when explicitly asked AND configured.
    if not s.use_proxies or not s.proxy_url:
        return None
    return {"http": s.proxy_url, "https": s.proxy_url}


async def fetch_html(
    url: str,
    *,
    referer: str | None = None,
    timeout: float = 25.0,
    raise_for_status: bool = True,
) -> str:
    """GET the URL and return the decoded HTML/text body."""
    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    proxies = _proxies()
    async with AsyncSession(impersonate=_BROWSER_IMPERSONATION) as s:
        try:
            r = await s.get(url, headers=headers, proxies=proxies, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[cffi] GET {url} failed: {exc}")
            raise
    if raise_for_status and r.status_code >= 400:
        raise RuntimeError(f"[cffi] {url} → HTTP {r.status_code}")
    return r.text


async def fetch_bytes(
    url: str,
    *,
    referer: str | None = None,
    timeout: float = 20.0,
) -> tuple[int, bytes, str | None]:
    """GET the URL and return ``(status_code, content_bytes, content_type)``.

    Used by the photo fetcher; never raises on non-2xx — callers decide.
    """
    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    proxies = _proxies()
    async with AsyncSession(impersonate=_BROWSER_IMPERSONATION) as s:
        try:
            r = await s.get(url, headers=headers, proxies=proxies, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[cffi] photo {url} fetch failed: {exc}")
            return 0, b"", None
    return r.status_code, r.content, r.headers.get("content-type")


# Quiet the unused-import warning on the asyncio module — kept for typing
# hints / future enhancements.
_ = asyncio

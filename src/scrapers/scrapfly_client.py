"""Scrapfly API client — DataDome / Cloudflare bypass.

Used by the 4 sources whose anti-bot blocks Playwright outright (LeBonCoin,
SeLoger, PAP). Scrapfly handles TLS fingerprint, residential French IPs,
and JS challenge solving server-side; we just get rendered HTML back.

Credit-saving:
  - ``asp=True`` is mandatory for DataDome (LBC, SeLoger).
  - ``render_js=True`` is ~5× more expensive — only enable for SPA pages
    (LBC, SeLoger). PAP renders server-side once ASP unblocks.
  - ``country=fr`` is mandatory (DataDome geo-fingerprints).

Rate-limit hygiene:
  - Global semaphore caps concurrent Scrapfly calls (default 2). Their account
    throttles aggressively when >25 failed status codes/min hit the API.
  - On 429 with ``Retry-After``, we honor the header. Failing to do so risks an
    IP-level ban from Scrapfly.
  - Circuit-breaker: after N consecutive 429s, sleep ``circuit_breaker_secs``
    before retrying anything. Survives quota-exhaustion / account throttling.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from scrapfly import ScrapeApiResponse, ScrapeConfig, ScrapflyClient
from scrapfly.errors import ScrapflyError

from src.config import get_settings
from src.utils.logging import logger

_CLIENT: ScrapflyClient | None = None
# At most 2 concurrent Scrapfly requests across the whole scraper process.
# Their per-account fail threshold is ~25/min — going wide can trip a global
# 429 that hits ALL subsequent calls for several minutes.
_CONCURRENCY = 2
_SEM: asyncio.Semaphore | None = None
# Circuit-breaker state. Once tripped, every call sleeps until ``_open_until``.
_open_until: float = 0.0
_consecutive_429: int = 0
_CIRCUIT_TRIP_THRESHOLD = 3  # back-to-back 429s before tripping
_CIRCUIT_DEFAULT_SLEEP = 60.0  # fallback if Retry-After missing

_RETRY_AFTER_RE = re.compile(r"[Rr]etry[- ]?[Aa]fter[^\d]*(\d+)")


def _get_semaphore() -> asyncio.Semaphore:
    global _SEM
    if _SEM is None:
        _SEM = asyncio.Semaphore(_CONCURRENCY)
    return _SEM


def _get_client() -> ScrapflyClient:
    global _CLIENT
    settings = get_settings()
    if not settings.scrapfly_api_key:
        raise RuntimeError(
            "SCRAPFLY_API_KEY is empty — set it in .env to enable Scrapfly-backed scrapers"
        )
    if _CLIENT is None:
        _CLIENT = ScrapflyClient(key=settings.scrapfly_api_key)
    return _CLIENT


def _parse_retry_after(exc: Exception) -> float | None:
    """Pull a Retry-After hint out of a Scrapfly error message, if present.

    Scrapfly's 429 messages contain the rate window in human text; we parse
    optimistically and fall back to None when nothing matches.
    """
    msg = str(exc) or ""
    m = _RETRY_AFTER_RE.search(msg)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


async def fetch_html(
    url: str,
    *,
    render_js: bool = False,
    asp: bool = True,
    country: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Fetch a URL through Scrapfly and return the rendered HTML.

    Honors the circuit-breaker: when tripped, raises immediately so callers
    can skip the scrape cycle without burning more credits.
    """
    global _open_until, _consecutive_429

    now = time.monotonic()
    if now < _open_until:
        wait = _open_until - now
        raise RuntimeError(
            f"Scrapfly circuit-breaker open for {wait:.0f}s more (back-off after repeated 429)"
        )

    settings = get_settings()
    client = _get_client()
    cfg_kwargs: dict[str, Any] = {
        "url": url,
        "asp": asp,
        "render_js": render_js,
        "country": country or settings.scrapfly_country,
    }
    if extra:
        cfg_kwargs.update(extra)
    cfg = ScrapeConfig(**cfg_kwargs)

    async with _get_semaphore():
        logger.debug(
            f"[scrapfly] GET {url} (asp={asp} render_js={render_js} "
            f"country={cfg_kwargs['country']})"
        )
        try:
            result: ScrapeApiResponse = await client.async_scrape(cfg)
        except ScrapflyError as exc:
            msg = str(exc)
            if "429" in msg or "throttled" in msg.lower():
                _consecutive_429 += 1
                retry_after = _parse_retry_after(exc) or _CIRCUIT_DEFAULT_SLEEP
                if _consecutive_429 >= _CIRCUIT_TRIP_THRESHOLD:
                    _open_until = time.monotonic() + retry_after
                    logger.warning(
                        f"[scrapfly] 🛑 circuit-breaker TRIPPED — pausing all "
                        f"Scrapfly traffic for {retry_after:.0f}s (consecutive_429={_consecutive_429})"
                    )
                else:
                    logger.warning(
                        f"[scrapfly] 429 #{_consecutive_429} — backing off "
                        f"{retry_after:.0f}s before next call"
                    )
                    await asyncio.sleep(retry_after)
            raise
        # Success — reset failure counter.
        _consecutive_429 = 0
        return result.scrape_result["content"]

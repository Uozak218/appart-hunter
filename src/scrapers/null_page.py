"""No-op Page/Context for HTTP-only scrapers.

When a source exposes a clean JSON API (e.g. Espaces Atypiques WordPress REST),
launching a full Chromium tab is pure waste. ``BaseScraper.use_http_only=True``
swaps the browser for these stubs: ``fetch_listing_page`` / ``parse_detail``
still receive a ``page`` argument so the existing scraper contract is intact,
but the page does nothing — the scraper drives its own ``httpx`` calls.
"""

from __future__ import annotations


class NullPage:
    """Quacks like a Playwright Page but every method is a no-op."""

    url: str = ""

    async def goto(self, *_: object, **__: object) -> None:
        return None

    async def content(self) -> str:
        return ""

    async def wait_for_selector(self, *_: object, **__: object) -> None:
        return None

    async def wait_for_load_state(self, *_: object, **__: object) -> None:
        return None

    async def close(self) -> None:
        return None


class NullContext:
    async def new_page(self) -> NullPage:
        return NullPage()

    async def close(self) -> None:
        return None


class NullBrowser:
    """``async with``-compatible browser stub. Mirrors ``StealthBrowser`` surface."""

    def __init__(self, source: str) -> None:
        self.source = source
        self._ctx: NullContext | None = None

    async def __aenter__(self) -> NullContext:
        self._ctx = NullContext()
        return self._ctx

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._ctx is not None:
            await self._ctx.close()

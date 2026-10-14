"""Playwright Page/Context adapter backed by Scrapfly.

Lets a scraper that was written for ``playwright.async_api.Page`` keep its
``fetch_listing_page(page)`` / ``parse_detail(page, entry)`` signature even
when the backing fetch goes through Scrapfly. Only the handful of Page
methods our scrapers actually call are implemented; everything else is a
no-op or raises.

Used by ``BaseScraper`` when a scraper sets ``use_scrapfly = True``.
"""

from __future__ import annotations

from src.scrapers.scrapfly_client import fetch_html


class ScrapflyPage:
    """Minimal stand-in for ``playwright.async_api.Page``."""

    def __init__(self, *, render_js: bool, asp: bool, country: str | None = None) -> None:
        self._render_js = render_js
        self._asp = asp
        self._country = country
        self._html: str = ""
        self.url: str = ""

    async def goto(
        self,
        url: str,
        *,
        timeout: int | None = None,  # noqa: ARG002 — Scrapfly enforces its own
        wait_until: str | None = None,  # noqa: ARG002
        **_: object,
    ) -> None:
        self.url = url
        self._html = await fetch_html(
            url, render_js=self._render_js, asp=self._asp, country=self._country
        )

    async def content(self) -> str:
        return self._html

    # The two waits below are no-ops: Scrapfly already returned a fully
    # rendered DOM (when render_js=True) or a server-rendered page that
    # passed ASP. Nothing more to wait for client-side.
    async def wait_for_selector(self, *_: object, **__: object) -> None:
        return None

    async def wait_for_load_state(self, *_: object, **__: object) -> None:
        return None

    async def close(self) -> None:
        return None


class ScrapflyContext:
    """Minimal stand-in for ``playwright.async_api.BrowserContext``."""

    def __init__(self, *, render_js: bool, asp: bool, country: str | None = None) -> None:
        self._render_js = render_js
        self._asp = asp
        self._country = country

    async def new_page(self) -> ScrapflyPage:
        return ScrapflyPage(
            render_js=self._render_js, asp=self._asp, country=self._country
        )

    async def close(self) -> None:
        return None


class ScrapflyBrowser:
    """``async with`` context manager that yields a ``ScrapflyContext``.

    Mirrors the surface of ``StealthBrowser`` so ``BaseScraper.run()`` can use
    either backend interchangeably.
    """

    def __init__(
        self,
        source: str,
        *,
        render_js: bool = False,
        asp: bool = True,
        country: str | None = None,
    ) -> None:
        self.source = source
        self._ctx: ScrapflyContext | None = None
        self._render_js = render_js
        self._asp = asp
        self._country = country

    async def __aenter__(self) -> ScrapflyContext:
        self._ctx = ScrapflyContext(
            render_js=self._render_js, asp=self._asp, country=self._country
        )
        return self._ctx

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._ctx is not None:
            await self._ctx.close()

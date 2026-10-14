"""Base scraper contract.

A scraper's only job:
  1. Load a lightweight listing page (cheap, high frequency).
  2. Parse a set of external IDs + URLs.
  3. For each ID not seen recently, fetch the detail page + photos.
  4. Normalise to a :class:`RawListing` and return it.

The scheduler drives cadence; the base class handles:
  - Health tracking (consecutive failures, alerts).
  - Dedup against DB (so we only fetch *new* or *changed* listings).
  - Pre-filter on zone *before* paying for detail fetches.
  - Parallel detail fetches with a per-source semaphore.
  - Jittered delays between detail fetches (Playwright only).
"""

from __future__ import annotations

import asyncio
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from playwright.async_api import BrowserContext, Page
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import SearchCriteria, get_criteria
from src.models.listing import Listing
from src.models.scraper_health import ScraperHealth
from src.schemas.listing import RawListing
from src.scrapers.null_page import NullBrowser
from src.scrapers.scrapfly_page import ScrapflyBrowser
from src.scrapers.stealth import StealthBrowser
from src.utils.geo import zone_rule_for
from src.utils.logging import logger
from src.utils.metro import is_paris_intramuros


@dataclass
class ScrapeOutcome:
    source: str
    new_listings: list[RawListing] = field(default_factory=list)
    seen_count: int = 0
    errors: list[str] = field(default_factory=list)


_POSTCODE_RE = re.compile(r"\b((?:75|77|78|9[1-5])\d{3})\b")


class BaseScraper(ABC):
    source: str = ""
    listing_timeout_ms: int = 30_000
    detail_timeout_ms: int = 30_000
    # How many detail fetches to run in parallel per scrape cycle. 5 keeps
    # Chromium tabs / Scrapfly calls bounded while cutting wall-clock latency
    # by ~5× vs sequential.
    detail_concurrency: int = 5

    # Anti-bot backend toggle. When True, the scraper fetches HTML through
    # Scrapfly (DataDome / Cloudflare bypass) instead of the local Playwright
    # browser. The scraper's ``fetch_listing_page`` / ``parse_detail`` signatures
    # are unchanged — they receive a ScrapflyPage that quacks like a Page.
    use_scrapfly: bool = False
    # Only enable on SPA pages — ~5× the Scrapfly credit cost.
    scrapfly_render_js: bool = False
    # HTTP-only mode for sources with a clean JSON/REST API — skip Chromium
    # entirely. The scraper drives its own httpx calls; the ``page`` argument
    # is a no-op stub.
    use_http_only: bool = False

    def __init__(self, session: AsyncSession, criteria: SearchCriteria | None = None) -> None:
        self.session = session
        self.criteria = criteria or get_criteria()

    # ---- subclass contract ------------------------------------------------

    @abstractmethod
    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        """Return a list of `{"external_id": ..., "url": ...}` dicts from the listing page."""

    @abstractmethod
    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        """Fetch + parse the detail page for `entry`. Return None if unparseable."""

    # ---- helpers ----------------------------------------------------------

    async def _already_seen_external_ids(self, external_ids: list[str]) -> set[str]:
        if not external_ids:
            return set()
        result = await self.session.execute(
            select(Listing.external_id).where(
                Listing.source == self.source, Listing.external_id.in_(external_ids)
            )
        )
        return {row[0] for row in result.all()}

    async def _delay(self, low: float = 3.0, high: float = 8.0) -> None:
        await asyncio.sleep(random.uniform(low, high))

    # ---- orchestration ----------------------------------------------------

    def _browser(self):
        """Return an ``async with``-compatible browser/context manager.

        Priority: HTTP-only (no browser at all) > Scrapfly (DataDome/CF bypass) >
        local Playwright stealth (default).
        """
        if self.use_http_only:
            return NullBrowser(self.source)
        if self.use_scrapfly:
            return ScrapflyBrowser(self.source, render_js=self.scrapfly_render_js)
        return StealthBrowser(self.source)

    def _quick_postcode(self, entry: dict[str, Any]) -> str | None:
        """Best-effort postcode from cheap data we already have (no detail fetch).

        Subclasses can override (e.g. LBC has it in the embedded ad payload).
        Default: trust ``entry["postal_code"]`` if the scraper set it during
        ``fetch_listing_page``; otherwise grep a 75XXX out of the entry URL.
        """
        pc = entry.get("postal_code")
        if pc:
            return str(pc)
        url = entry.get("url") or ""
        m = _POSTCODE_RE.search(url)
        return m.group(1) if m else None

    def _pre_filter_zone(self, entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """Drop entries whose postcode is provably out-of-zone — *before* paying
        for a detail fetch. Conservative: when we can't determine the postcode,
        the entry is kept (the existing post-detail zone gate still runs)."""
        line14_enabled = self.criteria.zones.line14_proximity.enabled
        kept: list[dict[str, Any]] = []
        skipped = 0
        for e in entries:
            pc = self._quick_postcode(e)
            if pc is None:
                kept.append(e)
                continue
            if zone_rule_for(self.criteria, pc) is not None:
                kept.append(e)
                continue
            # Postcode not in include list. Keep only if line-14 expansion could
            # still save it (i.e. listing is intramuros and might be near L14).
            if line14_enabled and is_paris_intramuros(pc):
                kept.append(e)
                continue
            skipped += 1
        return kept, skipped

    def _should_fetch_detail(self, entry: dict[str, Any]) -> bool:
        """Whether a detail fetch is worth paying for. Default: always.

        Scrapers whose listing page already carries enough to reject a listing
        (recency, price, surface, zone) override this to avoid burning Scrapfly
        credits on stale or non-matching listings — see ``leboncoin``.
        """
        return True

    async def _fetch_details_parallel(
        self, ctx: Any, new_entries: list[dict[str, Any]], outcome: ScrapeOutcome
    ) -> list[RawListing]:
        """Fetch every detail concurrently with a semaphore + per-backend delay.

        Playwright sources still get a small post-fetch delay to look human;
        Scrapfly/HTTP-only skip the delay (Scrapfly throttles upstream, and the
        WP REST API doesn't care).

        Scrapfly sources are forced sequential (concurrency=1) because their
        account-level fail-rate limit (~25/min) is easy to trip and triggers
        a global throttle that affects every other Scrapfly source too.
        """
        effective_concurrency = 1 if self.use_scrapfly else self.detail_concurrency
        sem = asyncio.Semaphore(effective_concurrency)
        use_delay = not (self.use_scrapfly or self.use_http_only)
        results: list[RawListing] = []

        async def _one(entry: dict[str, Any]) -> RawListing | None:
            async with sem:
                try:
                    raw = await self._fetch_detail(ctx, entry)
                except Exception as exc:  # noqa: BLE001
                    outcome.errors.append(f"{entry.get('external_id')}: {exc}")
                    logger.warning(f"[{self.source}] detail parse failed {entry.get('url')}: {exc}")
                    raw = None
                if use_delay:
                    await self._delay()
                return raw

        gathered = await asyncio.gather(*(_one(e) for e in new_entries))
        results.extend(r for r in gathered if r is not None)
        return results

    async def run(self) -> ScrapeOutcome:
        outcome = ScrapeOutcome(source=self.source)
        try:
            async with self._browser() as ctx:
                entries = await self._list_entries(ctx)
                outcome.seen_count = len(entries)
                if not entries:
                    logger.warning(f"[{self.source}] listing page returned 0 entries")
                    await self._record_health(success=True, count=0, error=None)
                    return outcome

                known = await self._already_seen_external_ids([e["external_id"] for e in entries])
                new_entries = [e for e in entries if e["external_id"] not in known]
                pre_count = len(new_entries)

                # NEW: zone pre-filter using cheap listing-page data — avoids
                # paying for ~15 wasted detail fetches when an entire source's
                # batch is out-of-zone (e.g. Orpi's nationwide premium stock).
                new_entries, zone_skipped = self._pre_filter_zone(new_entries)
                pre_detail = len(new_entries)
                new_entries = [e for e in new_entries if self._should_fetch_detail(e)]
                detail_skipped = pre_detail - len(new_entries)
                logger.info(
                    f"[{self.source}] {len(entries)} entries, {pre_count} new "
                    f"({zone_skipped} pre-zone, {detail_skipped} pre-detail skipped), "
                    f"{len(new_entries)} to detail"
                )

                # NEW: parallel detail fetches (5× concurrency, Playwright delay
                # only when needed).
                fetched = await self._fetch_details_parallel(ctx, new_entries, outcome)
                outcome.new_listings.extend(fetched)

                await self._record_health(success=True, count=len(entries), error=None)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[{self.source}] scrape failed: {exc}")
            outcome.errors.append(str(exc))
            await self._record_health(success=False, count=0, error=str(exc))
        return outcome

    async def _list_entries(self, ctx: BrowserContext) -> list[dict[str, str]]:
        page = await ctx.new_page()
        try:
            entries = await self.fetch_listing_page(page)
        finally:
            await page.close()
        # Filter out entries with obviously missing keys
        return [e for e in entries if e.get("external_id") and e.get("url")]

    async def _fetch_detail(self, ctx: BrowserContext, entry: dict[str, str]) -> RawListing | None:
        page = await ctx.new_page()
        try:
            return await self.parse_detail(page, entry)
        finally:
            await page.close()

    # ---- health -----------------------------------------------------------

    async def _record_health(
        self, *, success: bool, count: int, error: str | None
    ) -> ScraperHealth:
        result = await self.session.execute(
            select(ScraperHealth).where(ScraperHealth.source == self.source)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = ScraperHealth(source=self.source)
            self.session.add(row)
            await self.session.flush()

        row.total_runs += 1
        now = datetime.now(UTC)
        if success:
            row.consecutive_failures = 0
            row.last_success_at = now
            row.last_listings_count = count
            row.last_error = None
            row.alerted = False
        else:
            row.consecutive_failures += 1
            row.total_failures += 1
            row.last_failure_at = now
            row.last_error = (error or "")[:2000]
        await self.session.commit()
        return row


def extract_text(text: Any) -> str:
    return (text or "").strip() if isinstance(text, str) else ""

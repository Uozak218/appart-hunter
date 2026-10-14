"""In-process fan-out notification queue.

Producers (scoring pipeline) drop ScoredListing events here.
A single consumer task drains the queue and pushes to Telegram + Discord (+
ntfy.sh if configured) in parallel.

Dedup is two-layered:
  1. In-memory 1h window (cheap, catches same-session duplicates from
     multiple scrapers hitting the same listing).
  2. Persistent check against ``listings.status``: if the listing was already
     marked ``notified`` we skip. On successful fan-out we flip the status to
     ``notified``. That makes alerts robust across restarts and across the
     re-scoring safety net.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

from sqlalchemy import select

from src.models.listing import Listing, ListingStatus
from src.notifier.discord import DiscordNotifier
from src.notifier.ntfy import NtfyNotifier
from src.notifier.telegram import TelegramNotifier
from src.schemas.listing import ScoredListing
from src.storage.database import get_session_factory
from src.utils.logging import logger

DEDUP_WINDOW_SECONDS = 3600  # 1h
# Set by /admin/pause — when > now, the worker skips pushes.
_pause_until_ts: float = 0.0


class NotificationQueue:
    def __init__(
        self,
        telegram: TelegramNotifier | None = None,
        discord: DiscordNotifier | None = None,
        ntfy: NtfyNotifier | None = None,
    ) -> None:
        self._queue: asyncio.Queue[ScoredListing] = asyncio.Queue()
        self._recent: OrderedDict[int, float] = OrderedDict()
        self.telegram = telegram or TelegramNotifier()
        self.discord = discord or DiscordNotifier()
        self.ntfy = ntfy or NtfyNotifier()
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    async def enqueue(self, item: ScoredListing) -> None:
        now = time.time()
        self._sweep(now)
        if item.listing_id in self._recent:
            logger.debug(f"Skipping duplicate alert for listing {item.listing_id}")
            return
        self._recent[item.listing_id] = now
        await self._queue.put(item)

    def _sweep(self, now: float) -> None:
        cutoff = now - DEDUP_WINDOW_SECONDS
        while self._recent:
            k, ts = next(iter(self._recent.items()))
            if ts < cutoff:
                self._recent.popitem(last=False)
            else:
                break

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopped.clear()
            self._task = asyncio.create_task(self._run(), name="notifier-queue")

    async def stop(self) -> None:
        self._stopped.set()
        await self._queue.put(None)  # type: ignore[arg-type]
        if self._task is not None:
            await self._task
        await self.telegram.close()
        await self.discord.close()
        await self.ntfy.close()

    async def _run(self) -> None:
        logger.info("Notifier queue started")
        while not self._stopped.is_set():
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                break
            if item is None:
                break
            try:
                # Paused?
                if time.time() < _pause_until_ts:
                    logger.info(
                        f"Notifier paused until {_pause_until_ts}; skipping listing {item.listing_id}"
                    )
                    continue

                # Persistent dedup: skip if already marked notified in DB.
                if await _already_notified(item.listing_id):
                    logger.debug(f"Listing {item.listing_id} already notified in DB — skipping")
                    continue

                await asyncio.gather(
                    self.telegram.send_listing(item),
                    self.discord.send_listing(item),
                    self.ntfy.send_listing(item),
                    return_exceptions=True,
                )
                await _mark_notified(item.listing_id)
                logger.info(
                    f"Alert sent for listing {item.listing_id} "
                    f"(score={item.score}, urgent={item.is_urgent})"
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"Failed to push alert: {exc}")
        logger.info("Notifier queue stopped")

    async def send_system(self, text: str, urgent: bool = False) -> None:
        """Send a plain-text system message (scraper down, budget warning...)."""
        await asyncio.gather(
            self.telegram.send_text(text, silent=not urgent),
            self.discord.send_text(text),
            self.ntfy.send_text(text, urgent=urgent),
            return_exceptions=True,
        )


async def _already_notified(listing_id: int) -> bool:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Listing.status).where(Listing.id == listing_id))
        status = result.scalar_one_or_none()
        return status == ListingStatus.notified


async def _mark_notified(listing_id: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Listing).where(Listing.id == listing_id))
        listing = result.scalar_one_or_none()
        if listing is None:
            return
        listing.status = ListingStatus.notified
        await session.commit()


def pause_until(ts: float) -> None:
    """Mute the notifier until the given POSIX timestamp."""
    global _pause_until_ts
    _pause_until_ts = ts


def paused_until() -> float:
    return _pause_until_ts


def resume() -> None:
    global _pause_until_ts
    _pause_until_ts = 0.0


_queue_singleton: NotificationQueue | None = None


def get_notification_queue() -> NotificationQueue:
    global _queue_singleton
    if _queue_singleton is None:
        _queue_singleton = NotificationQueue()
    return _queue_singleton

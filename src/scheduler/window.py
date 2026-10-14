"""Active scraping window.

Only scrape when new listings actually appear and the user can react — default
Mon-Sat 08:00-19:00 (``settings.tz``). Nights and Sundays off. This alone cuts
~60% of the Scrapfly burn vs running 24/7, and you don't miss anything you could
have acted on anyway.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import get_settings


def within_window(
    now: datetime,
    *,
    enabled: bool,
    start_hour: int,
    end_hour: int,
    last_weekday: int,
) -> bool:
    """Pure check — ``now`` must already be in the target timezone.

    ``last_weekday`` uses Python's Mon=0 .. Sun=6. ``end_hour`` is exclusive.
    """
    if not enabled:
        return True
    if now.weekday() > last_weekday:
        return False
    return start_hour <= now.hour < end_hour


def is_within_scrape_window(now: datetime | None = None) -> bool:
    """Settings-aware wrapper used by the scheduler."""
    s = get_settings()
    tz = ZoneInfo(s.tz)
    now = now.astimezone(tz) if now is not None else datetime.now(tz)
    return within_window(
        now,
        enabled=s.scrape_window_enabled,
        start_hour=s.scrape_window_start_hour,
        end_hour=s.scrape_window_end_hour,
        last_weekday=s.scrape_window_last_weekday,
    )

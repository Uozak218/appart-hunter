from datetime import datetime
from zoneinfo import ZoneInfo

from src.scheduler.window import within_window

PARIS = ZoneInfo("Europe/Paris")


def _w(dt, **over):
    base = {"enabled": True, "start_hour": 8, "end_hour": 19, "last_weekday": 5}
    base.update(over)
    return within_window(dt, **base)


def test_weekday_within_hours_is_open():
    # Mon 2026-06-15 10:00
    assert _w(datetime(2026, 6, 15, 10, tzinfo=PARIS)) is True


def test_before_open_hour_is_closed():
    assert _w(datetime(2026, 6, 15, 7, 59, tzinfo=PARIS)) is False


def test_at_open_hour_is_open():
    assert _w(datetime(2026, 6, 15, 8, 0, tzinfo=PARIS)) is True


def test_end_hour_is_exclusive():
    assert _w(datetime(2026, 6, 15, 19, 0, tzinfo=PARIS)) is False
    assert _w(datetime(2026, 6, 15, 18, 59, tzinfo=PARIS)) is True


def test_saturday_is_open():
    # Sat 2026-06-20
    assert _w(datetime(2026, 6, 20, 10, tzinfo=PARIS)) is True


def test_sunday_is_closed():
    # Sun 2026-06-21
    assert _w(datetime(2026, 6, 21, 10, tzinfo=PARIS)) is False


def test_disabled_is_always_open():
    assert _w(datetime(2026, 6, 21, 3, tzinfo=PARIS), enabled=False) is True

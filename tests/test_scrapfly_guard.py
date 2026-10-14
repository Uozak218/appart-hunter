from datetime import UTC, datetime, timedelta

from src.scrapers.scrapfly_guard import evaluate_overspend

START = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
END = START + timedelta(days=30)


def _ev(used, now_offset_days, ratio=1.5, limit=200_000):
    return evaluate_overspend(
        used=used,
        limit=limit,
        period_start=START,
        period_end=END,
        now=START + timedelta(days=now_offset_days),
        ratio=ratio,
    )


def test_on_pace_does_not_trip():
    # ~1/3 into the period, ~1/3 of the budget used → on pace.
    over, expected = _ev(used=66_000, now_offset_days=10)
    assert over is False
    assert 60_000 < expected < 72_000


def test_well_ahead_of_pace_trips():
    # 1 day in but already 60% spent → way ahead → trip.
    over, _ = _ev(used=120_000, now_offset_days=1)
    assert over is True


def test_early_tiny_burst_does_not_trip():
    # Half a day in, only 3% spent → below the floor → no false trip.
    over, _ = _ev(used=6_000, now_offset_days=0.5)
    assert over is False


def test_reset_backlog_does_not_false_trip():
    # The real incident: ~10k (5%) burned in the first hours (reset backlog).
    # Below the 15% floor → must NOT trip.
    over, _ = _ev(used=10_290, now_offset_days=0.25)
    assert over is False


def test_sustained_overspend_past_floor_trips():
    # 2 days in, already 40% spent → past the 15% floor AND way ahead of pace.
    over, _ = _ev(used=80_000, now_offset_days=2)
    assert over is True


def test_missing_data_is_safe():
    over, _ = evaluate_overspend(
        used=None,
        limit=200_000,
        period_start=START,
        period_end=END,
        now=START + timedelta(days=1),
        ratio=1.5,
    )
    assert over is False

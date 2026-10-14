"""Tests for arrondissement-level score normalization."""

from __future__ import annotations

from src.scoring.pipeline import _normalise_score


def test_penalises_cachet_heavy_districts():
    # 7e and 16e lose points because cachet is the baseline there
    for arr in (7, 8, 16):
        assert _normalise_score(82, arr) < 82


def test_boosts_working_class_districts():
    # 18e, 19e, 20e get a small bump when Claude sees genuine cachet
    for arr in (18, 19, 20):
        assert _normalise_score(70, arr) > 70


def test_small_shift_magnitude():
    # Shifts should never exceed 4 points — calibration, not override
    for arr in range(1, 21):
        for s in (10, 50, 90):
            delta = _normalise_score(s, arr) - s
            assert -4 <= delta <= 4


def test_clamped_to_valid_range():
    # A 99 + boost stays <= 100; a 2 + penalty stays >= 0
    assert _normalise_score(99, 19) <= 100
    assert _normalise_score(2, 7) >= 0


def test_unknown_arr_returns_unchanged():
    assert _normalise_score(75, None) == 75
    assert _normalise_score(75, 99) == 75  # 99th arr doesn't exist


def test_neutral_arr_unchanged():
    # Arr with shift == 0 leaves the score alone
    assert _normalise_score(80, 9) == 80
    assert _normalise_score(80, 11) == 80
    assert _normalise_score(80, 15) == 80

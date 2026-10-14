"""Line-14 proximity tests.

Covers:
- haversine distance
- nearest_line14_station returns the closest one
- is_near_line14_station honours the 5 min walking budget (400 m by default)
- The zone gate accepts a listing outside zones.include if it's near line 14,
  and keeps rejecting a listing outside intramuros even if it is.
"""

from __future__ import annotations

from src.config import Line14Proximity, SearchCriteria, ZoneCriteria, ZoneRule
from src.utils.geo import is_in_target_zone
from src.utils.metro import (
    haversine_meters,
    is_near_line14_station,
    is_paris_intramuros,
    nearest_line14_station,
)

# Reference points
GARE_DE_LYON = (48.8443, 2.3737)  # literally on the station
BASTILLE = (48.8532, 2.3692)  # 75012 line 1/5/8, ~1 km from Gare de Lyon line 14


def test_haversine_symmetric():
    d1 = haversine_meters(*GARE_DE_LYON, *BASTILLE)
    d2 = haversine_meters(*BASTILLE, *GARE_DE_LYON)
    assert abs(d1 - d2) < 1e-3
    # Gare de Lyon ↔ Bastille ≈ 1 km, not 10 km
    assert 800 < d1 < 1300


def test_nearest_station_hits_gare_de_lyon():
    lat, lon = GARE_DE_LYON
    result = nearest_line14_station(lat, lon)
    assert result is not None
    station, dist = result
    assert station.name == "Gare de Lyon"
    assert dist < 50  # right on top


def test_is_near_line14_station_at_5min():
    # A point ~350m north of Gare de Lyon is within the 5 min / 400 m budget
    near = (48.8473, 2.3737)  # +0.003 latitude ~= 330 m
    assert is_near_line14_station(*near, max_walk_minutes=5.0)


def test_is_near_line14_station_rejects_bastille():
    # Bastille is too far from any line-14 station at 5 min
    assert not is_near_line14_station(*BASTILLE, max_walk_minutes=5.0)


def test_is_near_line14_station_handles_none():
    assert not is_near_line14_station(None, None)


def test_is_paris_intramuros():
    assert is_paris_intramuros("75011")
    assert is_paris_intramuros("75001")
    assert is_paris_intramuros("75020")
    assert not is_paris_intramuros("75021")
    assert not is_paris_intramuros("92100")
    assert not is_paris_intramuros(None)
    assert not is_paris_intramuros("")


def _criteria_with_line14() -> SearchCriteria:
    return SearchCriteria(
        zones=ZoneCriteria(
            include=[ZoneRule(code="75011")],
            line14_proximity=Line14Proximity(enabled=True, max_walk_minutes=5.0),
        ),
    )


def test_zone_gate_accepts_line14_match_outside_include_list():
    """75012 not in zones.include, but Gare de Lyon is 5 min away → accept."""
    c = _criteria_with_line14()
    assert is_in_target_zone(c, postcode="75012", lat=GARE_DE_LYON[0], lon=GARE_DE_LYON[1])


def test_zone_gate_rejects_far_from_line14():
    """75016 with coords in 75016, line-14 enabled but too far → reject."""
    c = _criteria_with_line14()
    assert not is_in_target_zone(c, postcode="75016", lat=48.8530, lon=2.2700)


def test_zone_gate_still_honors_explicit_include():
    """75011 is explicitly included, so it passes regardless of line-14."""
    c = _criteria_with_line14()
    assert is_in_target_zone(c, postcode="75011")


def test_zone_gate_rejects_non_intramuros_even_near_line14():
    """Saint-Ouen (93) has a line-14 stop but we only want intramuros."""
    c = _criteria_with_line14()
    # approximate Saint-Ouen coords — outside 75xxx intramuros
    assert not is_in_target_zone(c, postcode="93400", lat=48.906, lon=2.333)


def test_zone_gate_line14_disabled_by_default():
    """With the default criteria (no line14_proximity), 75012 is rejected."""
    c = SearchCriteria(zones=ZoneCriteria(include=[ZoneRule(code="75011")]))
    assert not is_in_target_zone(c, postcode="75012", lat=GARE_DE_LYON[0], lon=GARE_DE_LYON[1])

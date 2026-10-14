"""Paris Métro proximity helpers.

Line 14 is the modern automated line crossing Paris from Saint-Ouen in the
north to Orly in the south. A listing anywhere in Paris intramuros within a
5-minute walk of a line-14 station is worth considering even if it falls
outside the default ``zones.include`` list.

The ``is_near_line14_station`` helper computes a haversine distance between
the listing coordinates and every station. 5 minutes on foot ≈ 400 m at a
normal city pace (~80 m/min).
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import NamedTuple

WALK_METERS_PER_MIN = 80.0  # normal city walking pace


class Station(NamedTuple):
    name: str
    lat: float
    lon: float


# Line 14 stations inside Paris intramuros (post-2024 extensions).
# Coordinates in WGS84.
LINE_14_STATIONS: tuple[Station, ...] = (
    Station("Porte de Clichy", 48.8955, 2.3143),
    Station("Pont Cardinet", 48.8860, 2.3127),
    Station("Saint-Lazare", 48.8750, 2.3240),
    Station("Madeleine", 48.8704, 2.3244),
    Station("Pyramides", 48.8656, 2.3339),
    Station("Châtelet", 48.8580, 2.3475),
    Station("Gare de Lyon", 48.8444, 2.3737),
    Station("Bercy", 48.8398, 2.3793),
    Station("Cour Saint-Émilion", 48.8337, 2.3866),
    Station("Bibliothèque François Mitterrand", 48.8296, 2.3761),
    Station("Olympiades", 48.8274, 2.3664),
)


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two WGS84 points."""
    r = 6_371_000.0
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * r * asin(sqrt(a))


def walk_meters_budget(max_walk_minutes: float) -> float:
    return max_walk_minutes * WALK_METERS_PER_MIN


def nearest_line14_station(lat: float | None, lon: float | None) -> tuple[Station, float] | None:
    """Return the closest line-14 station with its distance in meters, or None."""
    if lat is None or lon is None:
        return None
    best: tuple[Station, float] | None = None
    for station in LINE_14_STATIONS:
        dist = haversine_meters(lat, lon, station.lat, station.lon)
        if best is None or dist < best[1]:
            best = (station, dist)
    return best


def is_near_line14_station(
    lat: float | None, lon: float | None, *, max_walk_minutes: float = 5.0
) -> bool:
    """True iff the coordinates are within ``max_walk_minutes`` of any line-14 station."""
    nearest = nearest_line14_station(lat, lon)
    if nearest is None:
        return False
    _, dist = nearest
    return dist <= walk_meters_budget(max_walk_minutes)


# Well-connected stations inside the 20e arrondissement: any listing within a
# short walk of one of these stations has a usable transit situation. Stations
# at the eastern fringe (Porte de Bagnolet, Porte de Montreuil) are excluded —
# they technically exist but the commute into central Paris is poor.
WELL_CONNECTED_STATIONS_20E: tuple[Station, ...] = (
    Station("Père-Lachaise", 48.8634, 2.3884),  # M2/M3
    Station("Gambetta", 48.8651, 2.3989),  # M3/M3bis
    Station("Pyrénées", 48.8736, 2.3838),  # M11
    Station("Ménilmontant", 48.8666, 2.3826),  # M2
    Station("Alexandre Dumas", 48.8569, 2.3953),  # M2
    Station("Philippe Auguste", 48.8584, 2.3853),  # M2
    Station("Avron", 48.8487, 2.3996),  # M2
    Station("Nation", 48.8483, 2.3958),  # M1/M2/M6/M9/RER A
    Station("Porte de Vincennes", 48.8462, 2.4108),  # M1
    Station("Buzenval", 48.8526, 2.4034),  # M9
)


def is_near_well_connected_metro_20e(
    lat: float | None, lon: float | None, *, max_walk_minutes: float = 6.0
) -> bool:
    """True iff coords are within ``max_walk_minutes`` of a well-connected 20e station."""
    if lat is None or lon is None:
        return False
    budget = walk_meters_budget(max_walk_minutes)
    for station in WELL_CONNECTED_STATIONS_20E:
        if haversine_meters(lat, lon, station.lat, station.lon) <= budget:
            return True
    return False


def is_paris_intramuros(postcode: str | None) -> bool:
    """Return True for any 75001..75020 postcode."""
    if not postcode or len(postcode) != 5 or not postcode.startswith("75"):
        return False
    try:
        arr = int(postcode[2:])
    except ValueError:
        return False
    return 1 <= arr <= 20

from types import SimpleNamespace

from src.config import load_criteria
from src.scoring.hard_filter import hard_prefilter


def _listing(**overrides):
    base = {
        "source": "leboncoin",
        "title": "T3 lumineux",
        "description": "Bel appartement parquet moulures.",
        "rent_cc": 1850,
        "surface_m2": 52,
        "rooms": 3,
        "property_type": "T3",
        "furnished": False,
        "floor": 4,
        "has_elevator": True,
        "postal_code": "75011",
        "latitude": None,
        "longitude": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_prefilter_passes_clean_listing(criteria_yaml):
    c = load_criteria(criteria_yaml)
    decision, reasons = hard_prefilter(_listing(), c)
    assert decision is True
    assert reasons


def test_prefilter_fails_rent_too_high(criteria_yaml):
    c = load_criteria(criteria_yaml)
    decision, reasons = hard_prefilter(_listing(rent_cc=3000), c)
    assert decision is False
    assert any("rent" in r.lower() for r in reasons)


def test_prefilter_fails_outside_zone(criteria_yaml):
    c = load_criteria(criteria_yaml)
    decision, reasons = hard_prefilter(_listing(postal_code="75016"), c)
    assert decision is False
    assert any("75016" in r for r in reasons)


def test_prefilter_fails_surface_too_small(criteria_yaml):
    c = load_criteria(criteria_yaml)
    decision, _ = hard_prefilter(_listing(surface_m2=30), c)
    assert decision is False


def test_prefilter_fails_furnished(criteria_yaml):
    c = load_criteria(criteria_yaml)
    decision, _ = hard_prefilter(_listing(furnished=True), c)
    assert decision is False


def test_prefilter_fails_ground_floor(criteria_yaml):
    c = load_criteria(criteria_yaml)
    decision, reasons = hard_prefilter(_listing(floor=0), c)
    assert decision is False
    assert any("RDC" in r or "ground" in r for r in reasons)


def test_prefilter_ambiguous_when_surface_missing(criteria_yaml):
    c = load_criteria(criteria_yaml)
    decision, _ = hard_prefilter(_listing(surface_m2=None), c)
    assert decision is None


def test_prefilter_ambiguous_when_type_unknown(criteria_yaml):
    c = load_criteria(criteria_yaml)
    decision, _ = hard_prefilter(_listing(property_type=None, rooms=None), c)
    assert decision is None


def test_prefilter_fails_on_excluded_keyword(criteria_yaml):
    c = load_criteria(criteria_yaml)
    decision, reasons = hard_prefilter(_listing(description="Idéal pour colocation à 3."), c)
    assert decision is False
    assert any("colocation" in r for r in reasons)

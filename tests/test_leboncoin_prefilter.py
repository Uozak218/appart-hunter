from datetime import datetime

from src.config import SearchCriteria
from src.scrapers.leboncoin import prefilter_ad

NOW = datetime(2026, 6, 16, 12, 0, 0)


def _crit():
    return SearchCriteria.model_validate(
        {
            "property": {
                "types": {"preferred": ["T3"], "acceptable": ["T2"]},
                "min_surface": {"T3": 45, "T2": 40},
                "max_rent_cc": 1901,
            },
            "zones": {
                "include": [{"code": "75011"}, {"code": "75020"}],
                "line14_proximity": {"enabled": False},
            },
        }
    )


def _ad(**over):
    ad = {
        "first_publication_date": "2026-06-15 10:00:00",
        "price": [1500],
        "attributes": [
            {"key": "rooms", "value": "3"},
            {"key": "square", "value": "50"},
        ],
        "location": {"zipcode": "75011", "lat": 48.86, "lng": 2.37},
    }
    ad.update(over)
    return ad


def _keep(ad, max_age=7):
    return prefilter_ad(ad, _crit(), max_age_days=max_age, now=NOW)[0]


def test_good_recent_in_budget_ad_is_kept():
    assert _keep(_ad()) is True


def test_stale_ad_is_skipped():
    # published 15 days before NOW, cutoff 7
    assert _keep(_ad(first_publication_date="2026-06-01 10:00:00")) is False


def test_over_budget_is_skipped():
    assert _keep(_ad(price=[2200])) is False


def test_studio_t1_is_skipped():
    ad = _ad(attributes=[{"key": "rooms", "value": "1"}, {"key": "square", "value": "50"}])
    assert _keep(ad) is False


def test_too_small_t3_is_skipped():
    ad = _ad(attributes=[{"key": "rooms", "value": "3"}, {"key": "square", "value": "42"}])
    assert _keep(ad) is False  # T3 needs 45


def test_out_of_zone_is_skipped():
    assert _keep(_ad(location={"zipcode": "75008", "lat": 48.87, "lng": 2.31})) is False


def test_missing_fields_are_kept_conservatively():
    assert _keep({"attributes": []}) is True


def test_recency_disabled_keeps_old_ad():
    assert _keep(_ad(first_publication_date="2026-01-01 10:00:00"), max_age=0) is True

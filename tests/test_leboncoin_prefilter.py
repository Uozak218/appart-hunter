from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from src.config import SearchCriteria
from src.scrapers.leboncoin import _build_listing_url, _lbc_locations, prefilter_ad

NOW = datetime(2026, 6, 16, 12, 0, 0)


def _crit():
    return SearchCriteria.model_validate(
        {
            "property": {
                "types": {"preferred": ["T1"], "acceptable": ["T2"]},
                "min_surface": {"T1": 22, "T2": 22},
                "max_rent_cc": 800,
                "furnished": False,
            },
            "zones": {
                "include": [
                    {"code": "92300", "city": "Levallois-Perret", "insee": "92044"},
                    {"code": "92110", "city": "Clichy", "insee": "92024"},
                    {"code": "92600", "city": "Asnières-sur-Seine", "insee": "92004"},
                    {"code": "93200", "city": "Saint-Denis", "insee": "93066"},
                    {"code": "93210", "city": "Saint-Denis", "insee": "93066"},
                ],
                "line14_proximity": {"enabled": False},
            },
        }
    )


def _ad(**over):
    ad = {
        "first_publication_date": "2026-06-15 10:00:00",
        "price": [750],
        "attributes": [
            {"key": "rooms", "value": "1"},
            {"key": "square", "value": "25"},
        ],
        "location": {"zipcode": "92110", "lat": 48.904, "lng": 2.306},
    }
    ad.update(over)
    return ad


def _keep(ad, max_age=7):
    return prefilter_ad(ad, _crit(), max_age_days=max_age, now=NOW)[0]


def test_studio_25m2_750_in_clichy_is_kept():
    assert _keep(_ad()) is True


def test_t2_in_budget_is_kept():
    ad = _ad(attributes=[{"key": "rooms", "value": "2"}, {"key": "square", "value": "30"}])
    assert _keep(ad) is True


def test_stale_ad_is_skipped():
    # published 15 days before NOW, cutoff 7
    assert _keep(_ad(first_publication_date="2026-06-01 10:00:00")) is False


def test_over_budget_is_skipped():
    assert _keep(_ad(price=[850])) is False


def test_t3_is_skipped():
    ad = _ad(attributes=[{"key": "rooms", "value": "3"}, {"key": "square", "value": "50"}])
    assert _keep(ad) is False


def test_too_small_studio_is_skipped():
    ad = _ad(attributes=[{"key": "rooms", "value": "1"}, {"key": "square", "value": "18"}])
    assert _keep(ad) is False  # T1 needs 22


def test_paris_listing_is_out_of_zone():
    assert _keep(_ad(location={"zipcode": "75011", "lat": 48.86, "lng": 2.37})) is False


def test_other_suburb_is_out_of_zone():
    assert _keep(_ad(location={"zipcode": "92000", "lat": 48.89, "lng": 2.20})) is False


def test_missing_fields_are_kept_conservatively():
    assert _keep({"attributes": []}) is True


def test_recency_disabled_keeps_old_ad():
    assert _keep(_ad(first_publication_date="2026-01-01 10:00:00"), max_age=0) is True


def test_locations_built_from_zones_deduped_by_insee():
    assert _lbc_locations(_crit()) == [
        "Levallois-Perret_92300",
        "Clichy_92110",
        "Asnières-sur-Seine_92600",
        "Saint-Denis_93200",
    ]


def test_listing_url_from_criteria():
    url = _build_listing_url(_crit())
    qs = parse_qs(urlsplit(url).query)
    assert qs["locations"] == [
        "Levallois-Perret_92300,Clichy_92110,Asnières-sur-Seine_92600,Saint-Denis_93200"
    ]
    assert qs["price"] == ["min-800"]
    assert qs["rooms"] == ["1-2"]
    assert qs["square"] == ["22-max"]
    assert qs["furnished"] == ["2"]  # non meublé
    assert qs["real_estate_type"] == ["2"]  # appartement
    assert "d_75" not in url

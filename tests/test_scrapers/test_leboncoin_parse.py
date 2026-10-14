"""Unit tests for LBC parsing helpers.

We can't hit leboncoin.fr in CI, so we test the pure parsing helpers directly.
"""

from __future__ import annotations

from src.scrapers.leboncoin import _find_ad_in_detail, _walk_for_ads


def _ad(list_id: int, subject: str) -> dict:
    return {
        "list_id": list_id,
        "subject": subject,
        "price": [1850],
        "body": "Bel appartement...",
        "attributes": [
            {"key": "rooms", "value_label": "3"},
            {"key": "square", "value_label": "52"},
        ],
        "location": {"zipcode": "75011", "lat": 48.85, "lng": 2.38, "city": "Paris"},
        "images": {"urls_large": ["https://img/1.jpg", "https://img/2.jpg"]},
    }


def test_walk_for_ads_simple():
    data = {"props": {"pageProps": {"searchData": {"ads": [_ad(1, "T3"), _ad(2, "T2")]}}}}
    ads = _walk_for_ads(data)
    assert [a["list_id"] for a in ads] == [1, 2]


def test_walk_for_ads_nested_multi_level():
    data = {"a": {"b": [{"c": {"ads": [_ad(42, "x")]}}]}}
    ads = _walk_for_ads(data)
    assert ads[0]["list_id"] == 42


def test_find_ad_in_detail():
    data = {"props": {"pageProps": {"classified": _ad(7, "Coquet T3")}}}
    ad = _find_ad_in_detail(data)
    assert ad is not None
    assert ad["list_id"] == 7


def test_find_ad_in_detail_none():
    assert _find_ad_in_detail({"random": {"nothing": [1, 2]}}) is None

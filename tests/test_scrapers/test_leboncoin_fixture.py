"""Test LBC parsing against a saved __NEXT_DATA__ blob.

Catches regressions in _walk_for_ads / _attr / detail-page extraction without
hitting the live site.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.scrapers.leboncoin import _walk_for_ads

FIXTURE = Path(__file__).parent.parent / "fixtures" / "leboncoin_listing.html"
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


def _load_next_data() -> dict:
    html = FIXTURE.read_text(encoding="utf-8")
    m = NEXT_DATA_RE.search(html)
    assert m is not None, "fixture must contain __NEXT_DATA__"
    return json.loads(m.group(1))


def test_walk_for_ads_finds_both_listings():
    data = _load_next_data()
    ads = _walk_for_ads(data)
    list_ids = sorted(a["list_id"] for a in ads)
    assert list_ids == [2887701234, 2887701235]


def test_extract_t3_attributes():
    data = _load_next_data()
    ads = _walk_for_ads(data)
    t3 = next(a for a in ads if a["list_id"] == 2887701234)
    assert t3["subject"].startswith("T3 haussmannien")
    assert t3["price"][0] == 1850

    attrs = {a["key"]: a["value_label"] for a in t3["attributes"]}
    assert attrs["rooms"] == "3"
    assert attrs["square"] == "52"
    assert attrs["floor_number"] == "4"
    assert attrs["elevator"] == "Oui"

    photos = t3["images"]["urls_large"]
    assert len(photos) == 3
    assert all(p.startswith("https://img.leboncoin.fr") for p in photos)


def test_extract_postcode_lat_lng():
    data = _load_next_data()
    ads = _walk_for_ads(data)
    t3 = next(a for a in ads if a["list_id"] == 2887701234)
    loc = t3["location"]
    assert loc["zipcode"] == "75011"
    assert loc["lat"] == 48.857
    assert loc["lng"] == 2.378

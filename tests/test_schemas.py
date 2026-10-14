import pytest
from pydantic import ValidationError

from src.schemas.listing import RawListing, ScoredListing


def test_raw_listing_minimum():
    r = RawListing(
        source="leboncoin",
        external_id="123",
        url="https://leboncoin.fr/ad/locations/123",
        title="T3 lumineux",
    )
    assert r.source == "leboncoin"
    assert r.photo_urls == []


def test_raw_listing_missing_required():
    with pytest.raises(ValidationError):
        RawListing(source="leboncoin", external_id="123", url="https://leboncoin.fr/x")


def test_scored_listing():
    raw = RawListing(
        source="leboncoin",
        external_id="1",
        url="https://leboncoin.fr/x",
        title="T3",
    )
    scored = ScoredListing(
        listing_id=1,
        raw=raw,
        score=88,
        highlights=["moulures"],
        red_flags=[],
        reasons=["cachet parisien"],
        is_urgent=True,
    )
    assert scored.is_urgent
    assert scored.score == 88

from src.schemas.listing import RawListing
from src.scoring.scam_detector import detect_scam


def _raw(**overrides) -> RawListing:
    base = {
        "source": "leboncoin",
        "external_id": "x",
        "url": "https://leboncoin.fr/x",
        "title": "T3 lumineux haussmannien",
        "description": "Bel appartement traversant, parquet et moulures.",
        "rent_cc": 1850,
        "surface_m2": 52,
        "rooms": 3,
        "property_type": "T3",
        "postal_code": "75011",
        "photo_urls": ["https://img.example/1.jpg"] * 6,
    }
    base.update(overrides)
    return RawListing(**base)


def test_clean_listing_passes():
    report = detect_scam(_raw())
    assert not report.is_scam
    assert report.score == 0


def test_western_union_in_description_flagged():
    report = detect_scam(_raw(description="Paiement par Western Union, je suis missionnaire."))
    assert report.is_scam
    assert any("mots-cl" in r for r in report.reasons)


def test_far_below_market_flagged():
    # 600€ for 60m² in Paris -> definitely scam
    report = detect_scam(_raw(rent_cc=600, surface_m2=60))
    assert report.is_scam
    assert any("anormal" in r for r in report.reasons)


def test_foreign_phone_only_doesnt_flag_alone():
    # Single foreign phone is suspicious but below threshold
    report = detect_scam(_raw(description="Pour visite, appelez le +44 7700 123456."))
    assert report.score >= 25
    assert any("téléphone" in r for r in report.reasons)


def test_low_price_low_photos_combined():
    report = detect_scam(_raw(rent_cc=1100, surface_m2=50, photo_urls=["only.jpg"]))
    # below market + few photos -> flagged
    assert report.score >= 30

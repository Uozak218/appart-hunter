from src.config import MarketPrices
from src.schemas.listing import RawListing
from src.scoring.scam_detector import detect_scam


def test_market_prices_per_arrondissement():
    mp = MarketPrices()
    assert mp.floor_for(1) >= 40  # 1er is pricey
    assert mp.floor_for(20) < mp.floor_for(1)  # 20e is cheaper
    assert mp.floor_for(None) == mp.default_eur_per_m2


def _raw(**o):
    base = {
        "source": "leboncoin",
        "external_id": "x",
        "url": "https://leboncoin.fr/x",
        "title": "T3 lumineux",
        "description": "Bel appartement.",
        "rent_cc": 1850,
        "surface_m2": 52,
        "postal_code": "75001",
        "photo_urls": ["a", "b", "c"],
    }
    base.update(o)
    return RawListing(**base)


def test_scam_uses_per_arr_floor_1er_stricter():
    """1200€/60m² = 20€/m² — suspicious in 75020 (floor 25), full arnaque in 75001 (floor 42)."""
    raw_20 = _raw(rent_cc=1200, surface_m2=60, postal_code="75020")
    raw_1 = _raw(rent_cc=1200, surface_m2=60, postal_code="75001")

    mp = MarketPrices()
    r_20 = detect_scam(raw_20, market_prices=mp)
    r_1 = detect_scam(raw_1, market_prices=mp)

    # 75001 triggers the "anormalement bas" tier (<60% of 42€/m²); 75020 doesn't.
    assert r_1.score > r_20.score
    assert r_1.is_scam
    assert not r_20.is_scam  # 20 / 25 = 80% = "sous le marché" but not scam tier


def test_scam_fallback_without_market_prices():
    """Legacy callers without market_prices use the Paris-wide 28€/m² floor."""
    raw = _raw(rent_cc=600, surface_m2=60, postal_code="75011")
    r = detect_scam(raw)
    assert r.is_scam


def test_postcode_floor_wins_for_suburbs():
    from src.config import MarketPrices

    mp = MarketPrices(default_eur_per_m2=22.0, eur_per_m2_by_postcode={"92110": 28.0})
    assert mp.floor_for(None, postcode="92110") == 28.0
    assert mp.floor_for(None, postcode="92700") == 22.0

from src.config import MarketPrices, QuartierOverride
from src.schemas.listing import RawListing
from src.scoring.scam_detector import detect_scam


def _raw(**o):
    base = {
        "source": "leboncoin",
        "external_id": "x",
        "url": "https://leboncoin.fr/x",
        "title": "T3 lumineux",
        "description": "Bel appartement.",
        "rent_cc": 1800,
        "surface_m2": 52,
        "postal_code": "75011",
        "photo_urls": ["a", "b", "c"],
    }
    base.update(o)
    return RawListing(**base)


def test_quartier_override_overrides_arrondissement():
    mp = MarketPrices(
        quartiers=[
            QuartierOverride(
                arrondissement=11,
                name="Oberkampf",
                eur_per_m2=31.0,
                keywords=["oberkampf"],
            )
        ]
    )
    # 75011 default is 28, quartier override is 31
    assert mp.floor_for(11, text="T3 Oberkampf République") == 31.0
    # Same arr but no matching keyword → arr default
    assert mp.floor_for(11, text="T3 Nation calme") == 28.0


def test_quartier_case_and_accent_insensitive():
    mp = MarketPrices(
        quartiers=[
            QuartierOverride(
                arrondissement=10,
                name="Canal",
                eur_per_m2=32.0,
                keywords=["canal saint-martin"],
            )
        ]
    )
    assert mp.floor_for(10, text="Charmant T3 — Canal Saint-Martin") == 32.0
    assert mp.floor_for(10, text="charmant t3 canal saint martin") == 32.0


def test_scam_uses_quartier_override():
    """A 1900€/60m² listing (31.6 €/m²) is fine in default 75011 (28 floor),
    but scored as 'sous le marché' when the Oberkampf quartier override (31) applies
    and the listing is far below.
    """
    mp = MarketPrices(
        quartiers=[
            QuartierOverride(
                arrondissement=11,
                name="Oberkampf",
                eur_per_m2=35.0,  # higher than per-arr 28
                keywords=["oberkampf"],
            )
        ]
    )
    # 1500€ / 60m² = 25 €/m². Under 75011 default (28) → "sous le marché".
    # With Oberkampf 35: 25/35 = 71% → still "sous le marché" but penalty bigger.
    raw_base = _raw(
        rent_cc=1500,
        surface_m2=60,
        title="T3 lumineux",
        description="Rue de la fontaine",
    )
    raw_oberk = _raw(
        rent_cc=1500,
        surface_m2=60,
        title="T3 lumineux Oberkampf",
        description="Rue de la fontaine au koenig",
    )

    r_base = detect_scam(raw_base, market_prices=mp)
    r_oberk = detect_scam(raw_oberk, market_prices=mp)

    # Oberkampf floor is higher → stricter → larger scam score
    assert r_oberk.score > r_base.score


def test_market_prices_unchanged_without_quartiers():
    mp = MarketPrices()  # built-in defaults, no quartiers
    assert mp.floor_for(11) == 28.0  # per-arr
    assert mp.floor_for(None) == mp.default_eur_per_m2

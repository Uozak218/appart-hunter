from src.notifier.templates import build_header, discord_embed, telegram_message
from src.schemas.listing import RawListing, ScoredListing


def _scored(**overrides) -> ScoredListing:
    raw = RawListing(
        source="leboncoin",
        external_id="1",
        url="https://leboncoin.fr/ad/locations/1",
        title="Magnifique T3 haussmannien",
        rent_cc=1850,
        surface_m2=52,
        property_type="T3",
        postal_code="75011",
        floor=4,
        has_elevator=True,
        photo_urls=["https://img.example/1.jpg"],
    )
    base = {
        "listing_id": 1,
        "raw": raw,
        "score": 88,
        "highlights": ["parquet ancien", "belle lumière"],
        "red_flags": [],
        "reasons": ["Gem: cachet parisien, très lumineux."],
        "is_urgent": True,
        "price_drop_pct": None,
    }
    base.update(overrides)
    return ScoredListing(**base)


def test_build_header_urgent():
    s = _scored(score=92, is_urgent=True)
    h = build_header(s)
    assert "URGENT" in h
    assert "92" in h


def test_build_header_normal():
    s = _scored(score=74, is_urgent=False)
    h = build_header(s)
    assert "URGENT" not in h
    assert "74" in h


def test_build_header_price_drop():
    s = _scored(score=74, is_urgent=False, price_drop_pct=5.0)
    h = build_header(s)
    assert "Baisse" in h


def test_telegram_message_contains_key_fields():
    s = _scored()
    msg = telegram_message(s)
    assert "Magnifique T3 haussmannien" in msg
    assert "1850" in msg
    assert "52" in msg
    assert "Paris 11e" in msg
    assert "parquet ancien" in msg
    assert "leboncoin" in msg.lower() or "LeBonCoin" in msg


def test_discord_embed_structure():
    s = _scored()
    e = discord_embed(s)
    assert e["url"].startswith("https://")
    assert any(f["name"] == "Prix (CC)" for f in e["fields"])
    assert e["image"]["url"].startswith("https://")

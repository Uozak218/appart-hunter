from src.scoring.poster_detector import detect_poster


def test_explicit_agency_keyword():
    assert (
        detect_poster(title="T3 Bastille", description="Honoraires à la charge du locataire: 850€.")
        == "agency"
    )


def test_explicit_private_keyword():
    assert (
        detect_poster(
            title="T3 à louer de particulier à particulier",
            description="Direct propriétaire, sans agence.",
        )
        == "private"
    )


def test_raw_owner_type_private():
    raw = {"ad": {"owner_type": "private"}}
    assert detect_poster(title="T3 x", description="", raw=raw) == "private"


def test_raw_owner_type_pro():
    raw = {"ad": {"owner_type": "pro"}}
    # raw signal wins over ambiguous text
    assert detect_poster(title="T3 x", description="très calme", raw=raw) == "agency"


def test_unknown_without_signals():
    assert detect_poster(title="T3 lumineux", description="Parquet, moulures.") == "unknown"


def test_known_agency_brand():
    assert detect_poster(title="T3 Orpi Bastille", description="") == "agency"

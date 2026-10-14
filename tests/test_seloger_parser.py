from src.scrapers.seloger import _extract_rent, _extract_rooms, _extract_surface


def test_extract_rent_eur_per_month():
    # Real shape from the page: "1 725,11 €/mois" (non-breaking spaces).
    assert _extract_rent("Appartement 1 725,11 €/mois charges comprises") == 1725.11


def test_extract_rent_charges_comprises_fallback():
    assert _extract_rent("Loyer 1 200 € CC à Paris") == 1200


def test_extract_rent_none_when_absent():
    assert _extract_rent("aucun prix ici") is None


def test_extract_surface():
    assert _extract_surface("Surface habitable 53.85 m² au total") == 53.85
    assert _extract_surface("53,9 m²") == 53.9
    assert _extract_surface("pas de surface") is None


def test_extract_rooms():
    assert _extract_rooms("Location Appartement T3 53 m²") == 3
    assert _extract_rooms("Bel appartement 3 pièces lumineux") == 3
    assert _extract_rooms("studio meublé") is None

from src.config import SearchCriteria, load_criteria
from src.utils.geo import (
    arrondissement_from_postcode,
    extract_postal_code,
    is_in_target_zone,
)


def test_arrondissement_from_postcode():
    assert arrondissement_from_postcode("75011") == 11
    assert arrondissement_from_postcode("75003") == 3
    assert arrondissement_from_postcode("75042") is None  # out of range
    assert arrondissement_from_postcode(None) is None
    assert arrondissement_from_postcode("92100") is None


def test_extract_postal_code():
    assert extract_postal_code("Bel appart 75011 Paris 11e") == "75011"
    assert extract_postal_code("no postcode here") is None


def test_is_in_target_zone(criteria_yaml):
    c = load_criteria(criteria_yaml)
    assert is_in_target_zone(c, postcode="75011")
    assert not is_in_target_zone(c, postcode="75016")
    assert not is_in_target_zone(c, postcode=None)


def test_20e_accepted_when_coords_missing():
    """Regression: 75020 requires metro proximity, which needs coords. Sources
    like Foncia never geocode (lat/lon None) — we must NOT silently drop those
    matches (a 1253€ vide T2 in the 20e was lost this way). Accept when coords
    are absent; still gate on proximity when coords ARE present."""
    c = SearchCriteria.model_validate(
        {
            "zones": {
                "include": [{"code": "75020", "require_well_connected_metro": True}],
                "line14_proximity": {"enabled": False},
            }
        }
    )
    # No coords (Foncia) → accepted (the fix).
    assert is_in_target_zone(c, postcode="75020", lat=None, lon=None)
    # Coords present but far from any well-connected 20e station → still rejected.
    assert not is_in_target_zone(c, postcode="75020", lat=48.80, lon=2.30)

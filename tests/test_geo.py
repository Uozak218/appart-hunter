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


def test_extract_postal_code_suburbs():
    assert extract_postal_code("Studio 26m² Clichy 92110") == "92110"
    assert extract_postal_code("Saint-Ouen-sur-Seine (93400)") == "93400"
    # Listing ids that merely look like 5 digits are not postcodes.
    assert extract_postal_code("annonce 12345") is None


def _saint_denis_criteria() -> SearchCriteria:
    return SearchCriteria.model_validate(
        {
            "zones": {
                "include": [
                    {"code": "92110", "city": "Clichy"},
                    {
                        "code": "93200",
                        "city": "Saint-Denis",
                        "include_bbox": {
                            "min_lat": 48.900,
                            "max_lat": 48.930,
                            "min_lon": 2.330,
                            "max_lon": 2.375,
                        },
                        "include_keywords": ["pleyel", "la plaine"],
                    },
                ],
            }
        }
    )


def test_suburb_postcode_in_zone():
    c = _saint_denis_criteria()
    assert is_in_target_zone(c, postcode="92110")
    assert not is_in_target_zone(c, postcode="75011")


def test_sub_area_uses_coords_when_present():
    c = _saint_denis_criteria()
    # Carrefour Pleyel → inside the bbox.
    assert is_in_target_zone(c, postcode="93200", lat=48.9193, lon=2.3429)
    # Basilique / centre-ville → outside, even if the text says "Pleyel".
    assert not is_in_target_zone(
        c, postcode="93200", lat=48.9356, lon=2.3597, text="à 10 min de Pleyel"
    )


def test_sub_area_falls_back_to_keywords_without_coords():
    c = _saint_denis_criteria()
    assert is_in_target_zone(c, postcode="93200", text="Studio quartier Pleyel, M14")
    assert is_in_target_zone(c, postcode="93200", text="La Plaine Saint-Denis")
    assert not is_in_target_zone(c, postcode="93200", text="Studio centre-ville")
    assert not is_in_target_zone(c, postcode="93200")

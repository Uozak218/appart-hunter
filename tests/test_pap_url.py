from src.config import SearchCriteria
from src.scrapers.pap import build_listing_url, extract_postcode, postcode_from_pap_url


def _crit(include=None):
    return SearchCriteria.model_validate(
        {
            "property": {
                "types": {"preferred": ["T1"], "acceptable": ["T2"]},
                "min_surface": {"T1": 22, "T2": 22},
                "max_rent_cc": 800,
            },
            "zones": {
                "include": include
                or [
                    {"code": "92300", "city": "Levallois-Perret", "insee": "92044"},
                    {"code": "92110", "city": "Clichy", "insee": "92024"},
                    {"code": "93200", "city": "Saint-Denis", "insee": "93066"},
                    {"code": "93210", "city": "Saint-Denis", "insee": "93066"},
                ],
            },
        }
    )


def test_listing_url_from_criteria():
    assert build_listing_url(_crit()) == (
        "https://www.pap.fr/annonce/locations-appartement-levallois-perret-92300-"
        "g43289g43269g43705-du-studio-au-2-pieces-jusqu-a-800-euros-a-partir-de-22-m2"
    )


def test_unknown_town_is_skipped():
    url = build_listing_url(
        _crit(
            [
                {"code": "99999", "city": "Nowhere", "insee": "99999"},
                {"code": "92110", "city": "Clichy", "insee": "92024"},
            ]
        )
    )
    assert url is not None
    assert "-clichy-92110-g43269-" in url
    assert build_listing_url(_crit([{"code": "99999", "city": "X", "insee": "99999"}])) is None


def test_postcode_parsing():
    assert postcode_from_pap_url(
        "https://www.pap.fr/annonces/appartement-clichy-92110-r439202345"
    ) == ("92110")
    assert (
        postcode_from_pap_url("https://www.pap.fr/annonces/appartement-saint-denis-93-r1") is None
    )
    assert extract_postcode("Location studio 25 m² Saint-Ouen-sur-Seine (93400)") == "93400"
    assert extract_postcode("Studio", url="https://www.pap.fr/annonces/a-colombes-92700-r12") == (
        "92700"
    )

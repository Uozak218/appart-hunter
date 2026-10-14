from src.config import SearchCriteria
from src.scrapers.seloger import (
    build_listing_url,
    extract_postcode,
    postcode_from_seloger_url,
    seloger_ci,
    url_is_out_of_zone,
)

_BASE = "https://www.seloger.com/annonces/locations/appartement"


def _crit():
    return SearchCriteria.model_validate(
        {
            "property": {
                "types": {"preferred": ["T1"], "acceptable": ["T2"]},
                "min_surface": {"T1": 22, "T2": 22},
                "max_rent_cc": 800,
            },
            "zones": {
                "include": [
                    {"code": "92300", "city": "Levallois-Perret", "insee": "92044"},
                    {"code": "92110", "city": "Clichy", "insee": "92024"},
                    {"code": "93400", "city": "Saint-Ouen-sur-Seine", "insee": "93070"},
                    {"code": "93200", "city": "Saint-Denis", "insee": "93066"},
                    {"code": "93210", "city": "Saint-Denis", "insee": "93066"},
                ],
                "line14_proximity": {"enabled": False},
            },
        }
    )


def test_seloger_ci_from_insee():
    assert seloger_ci("75056") == "750056"
    assert seloger_ci("92024") == "920024"
    assert seloger_ci("93070") == "930070"
    assert seloger_ci("") is None


def test_listing_url_from_criteria():
    url = build_listing_url(_crit())
    # One place per commune; Saint-Denis 93200/93210 share INSEE 93066.
    assert "places=[{ci:920044}|{ci:920024}|{ci:930070}|{ci:930066}]" in url
    assert "rooms=1,2" in url
    assert "types=1&" in url
    assert "price=NaN/800" in url
    assert "surface=22/NaN" in url
    assert "750056" not in url
    assert "{" not in url.replace("{ci:", "")  # no raw placeholder leaked


def test_postcode_from_paris_url():
    assert postcode_from_seloger_url(f"{_BASE}/paris-11eme-75/123456789.htm") == "75011"
    assert postcode_from_seloger_url(f"{_BASE}/paris-1er-75/1.htm") == "75001"
    assert postcode_from_seloger_url("https://www.seloger.com/whatever") is None


def test_postcode_from_suburb_url():
    c = _crit()
    assert postcode_from_seloger_url(f"{_BASE}/clichy-92/centre-ville/1234567.htm", c) == "92110"
    assert postcode_from_seloger_url(f"{_BASE}/levallois-perret-92/1234567.htm", c) == "92300"
    # Saint-Denis has two postcodes → ambiguous.
    assert postcode_from_seloger_url(f"{_BASE}/saint-denis-93/pleyel/1234567.htm", c) is None


def test_url_prefilter_zone():
    c = _crit()
    assert not url_is_out_of_zone(f"{_BASE}/clichy-92/1234567.htm", c)
    assert not url_is_out_of_zone(f"{_BASE}/saint-denis-93/1234567.htm", c)  # ambiguous → keep
    assert url_is_out_of_zone(f"{_BASE}/paris-11eme-75/1234567.htm", c)
    assert url_is_out_of_zone(f"{_BASE}/nanterre-92/1234567.htm", c)
    assert not url_is_out_of_zone("https://www.seloger.com/annonces/1234567.htm", c)


def test_extract_postcode_any_departement():
    c = _crit()
    text = "Location Appartement 1 pièce 25 m² Clichy (92110) 750 €/mois"
    assert extract_postcode(text, "", c) == "92110"
    assert extract_postcode("Studio Saint-Ouen 93400 lumineux", "", c) == "93400"
    assert extract_postcode("rien", f"{_BASE}/clichy-92/1234567.htm", c) == "92110"
    assert extract_postcode("Paris 11e (75011)", "", c) == "75011"

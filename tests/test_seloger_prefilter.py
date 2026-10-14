from src.config import SearchCriteria
from src.scrapers.seloger import postcode_from_seloger_url
from src.utils.geo import is_in_target_zone

_BASE = "https://www.seloger.com/annonces/locations/appartement"


def _crit():
    return SearchCriteria.model_validate(
        {
            "zones": {
                "include": [{"code": "75011"}, {"code": "75020"}],
                "line14_proximity": {"enabled": False},
            }
        }
    )


def test_postcode_from_seloger_url():
    assert postcode_from_seloger_url(f"{_BASE}/paris-11eme-75/123456789.htm") == "75011"
    assert postcode_from_seloger_url(f"{_BASE}/paris-1er-75/1.htm") == "75001"
    assert postcode_from_seloger_url(f"{_BASE}/paris-20eme-75/1.htm") == "75020"
    assert postcode_from_seloger_url("https://www.seloger.com/whatever") is None


def test_in_zone_arrondissement_passes():
    c = _crit()
    pc = postcode_from_seloger_url(f"{_BASE}/paris-11eme-75/1.htm")
    assert is_in_target_zone(c, postcode=pc)


def test_excluded_arrondissement_is_out_of_zone():
    c = _crit()
    pc = postcode_from_seloger_url(f"{_BASE}/paris-8eme-75/1.htm")
    assert pc == "75008"
    assert not is_in_target_zone(c, postcode=pc)

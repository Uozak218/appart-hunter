"""Town-based search building + postcode parsing for bienici / locservice /
superimmo / foncia (suburban zones from ``criteria.zones.include``)."""

from __future__ import annotations

import json

from src.config import SearchCriteria
from src.scrapers.bienici import (
    ad_postcode,
    build_filters,
    max_rooms,
    passes_card_filters,
    pick_zone_id,
    unique_towns,
)
from src.scrapers.foncia import parse_listing, town_url
from src.scrapers.locservice import parse_cards as locservice_cards
from src.scrapers.locservice import search_params
from src.scrapers.superimmo import listing_url, postcode_from_text, postcode_from_url
from src.scrapers.superimmo import parse_cards as superimmo_cards


def _criteria() -> SearchCriteria:
    return SearchCriteria.model_validate(
        {
            "property": {
                "types": {"preferred": ["T1"], "acceptable": ["T2"]},
                "min_surface": {"T1": 22, "T2": 25},
                "max_rent_cc": 800,
                "furnished": False,
            },
            "zones": {
                "include": [
                    {"code": "92300", "city": "Levallois-Perret", "insee": "92044"},
                    {"code": "92600", "city": "Asnières-sur-Seine", "insee": "92004"},
                    {"code": "93400", "city": "Saint-Ouen-sur-Seine", "insee": "93070"},
                    {"code": "93200", "city": "Saint-Denis", "insee": "93066"},
                    {"code": "93210", "city": "Saint-Denis", "insee": "93066"},
                ]
            },
        }
    )


# ---- shared helpers ---------------------------------------------------------


def test_unique_towns_dedupes_by_insee():
    towns = unique_towns(_criteria())
    assert [t.code for t in towns] == ["92300", "92600", "93400", "93200"]


def test_max_rooms_from_types():
    assert max_rooms(_criteria()) == 2


def test_passes_card_filters():
    c = _criteria()
    assert passes_card_filters(c, {"rent_cc": 790, "surface_m2": 24, "rooms": 1})
    assert passes_card_filters(c, {})  # unknown values pass
    assert not passes_card_filters(c, {"rent_cc": 801})
    assert not passes_card_filters(c, {"surface_m2": 21.5})
    assert not passes_card_filters(c, {"rooms": 3})
    assert not passes_card_filters(c, {"furnished": True})


# ---- bienici ----------------------------------------------------------------


def test_bienici_filters():
    f = build_filters(_criteria(), ["-86985", "-87922"])
    assert f["zoneIdsByTypes"] == {"zoneIds": ["-86985", "-87922"]}
    assert f["maxPrice"] == 800
    assert f["minArea"] == 22
    assert f["maxRooms"] == 2
    assert f["filterType"] == "rent"
    json.dumps(f)


def test_bienici_pick_zone_id_matches_insee_not_homonym():
    suggestions = [
        {"type": "city", "insee_code": "97411", "zoneIds": ["-192468"], "postalCodes": ["97400"]},
        {
            "type": "city",
            "insee_code": "93066",
            "insee_codes": ["93066"],
            "zoneIds": ["-87922"],
            "postalCodes": ["93200", "93210"],
        },
    ]
    rule = _criteria().zones.include[3]
    assert pick_zone_id(suggestions, rule) == "-87922"


def test_bienici_ad_postcode():
    assert ad_postcode({"postalCode": "92700", "city": "Colombes"}) == "92700"
    assert ad_postcode({"city": "Saint-Denis 93210"}) == "93210"
    assert ad_postcode({}) is None


# ---- locservice -------------------------------------------------------------


def test_locservice_search_params():
    params = search_params(_criteria(), "93066")
    assert ("search_ads[city][select]", "93066") in params
    assert ("search_ads[criteria][]", "unfurnished") in params
    assert ("search_ads[roomsNumbers][]", "2") in params
    assert ("search_ads[roomsNumbers][]", "3") not in params


_LOCSERVICE_HTML = """
<ul class="accommodation-ads">
 <li class="accommodation-ad" data-accommodation-id="1541391"><article>
  <h3><a class="stretched-link"
    href="https://www.locservice.fr/seine-saint-denis-93/location-studio-st-denis/1541391">
    Studio à louer</a></h3>
  <ul class="accommodation-ad-characteristics">
   <li class="accommodation-ad-characteristic">Saint-Denis (93200)</li>
   <li class="accommodation-ad-characteristic">27 m²</li>
   <li class="accommodation-ad-characteristic accommodation-ad-characteristic-price">
     1 050 € / mois</li>
  </ul></article></li>
 <li class="accommodation-ad"><article>
  <h3><a href="/hauts-de-seine-92/location-appartement-clichy/p-cf-110174460274">
    Appartement T1 meublé à louer</a></h3>
  <ul class="accommodation-ad-characteristics">
   <li class="accommodation-ad-characteristic">Clichy (92110)</li>
  </ul></article></li>
</ul>
"""


def test_locservice_cards_parse_any_postcode():
    cards = locservice_cards(_LOCSERVICE_HTML)
    assert [c["external_id"] for c in cards] == ["1541391", "p-cf-110174460274"]
    first, second = cards
    assert first["postal_code"] == "93200"
    assert first["surface_m2"] == 27
    assert first["rent_cc"] == 1050
    assert first["nature"] == "studio"
    assert second["postal_code"] == "92110"
    assert second["url"].startswith("https://www.locservice.fr/hauts-de-seine-92/")
    assert second["partner"] and second["furnished"]


# ---- superimmo --------------------------------------------------------------


def test_superimmo_listing_url():
    c = _criteria()
    assert listing_url(c, c.zones.include[1]) == (
        "https://www.superimmo.com/location/appartement/ile-de-france/hauts-de-seine/"
        "asnieres-sur-seine-92600?prix-max=800&surface-min=22"
    )
    assert "/seine-saint-denis/saint-ouen-sur-seine-93400?" in listing_url(c, c.zones.include[2])


def test_superimmo_postcodes():
    url = (
        "https://www.superimmo.com/annonces/location-appartement-29m-levallois-perret-92300-x128zj9"
    )
    assert postcode_from_url(url) == "92300"
    assert postcode_from_url("https://www.superimmo.com/annonces/x128zj9") is None
    assert postcode_from_text("Appartement • 29 m² | Saint-Ouen-sur-Seine (93400)") == "93400"


def test_superimmo_cards():
    html = """
    <section class="media"><div>
      <a href="/annonces/location-appartement-27m-clichy-92110-x128m69">x</a>
      <span>11</span><span>812 € CC</span>
      <span>Appartement • 27 m² • 1 pièce</span><span>Clichy (92110)</span>
    </div></section>"""
    (card,) = superimmo_cards(html)
    assert card["external_id"] == "x128m69"
    assert card["postal_code"] == "92110"
    assert card["rent_cc"] == 812
    assert card["surface_m2"] == 27
    assert card["rooms"] == 1


# ---- foncia -----------------------------------------------------------------


def test_foncia_town_url():
    c = _criteria()
    assert town_url(c.zones.include[2]) == (
        "https://fr.foncia.com/location/saint-ouen-sur-seine-93400/appartement"
    )
    assert town_url(c.zones.include[1]).endswith("/location/asnieres-sur-seine-92600/appartement")


def test_foncia_listing_postcodes():
    html = """
    <div class="foncia-card"><a href="/location/saint-ouen-sur-seine-93400/appartement/331716738.htm">
      <span>x7</span><span>724,88 €</span><span>/ mois CC</span>
      <span>Location Appartement 2 pièces 24.8 m² - Saint-ouen-sur-seine 93400</span>
      <span>24,8 m²</span><span>Saint-Ouen-sur-Seine (93400)</span></a></div>
    <div class="foncia-card"><a href="/location/saint-denis-93/appartement-meuble/30089-552.htm">
      <span>x9</span><span>1 206 €</span><span>Location Appartement 3 pièces 61.54 m²</span>
      <span>61,54 m²</span><span>Saint-Denis (93200)</span></a></div>
    <a href="/location/clichy-92110/parking/331669363.htm">parking</a>
    """
    a, b = parse_listing(html)
    assert (a["external_id"], a["postal_code"], a["rent_cc"], a["surface_m2"], a["rooms"]) == (
        "331716738",
        "93400",
        724.88,
        24.8,
        2,
    )
    assert a["furnished"] is False
    # Department-only URL → postcode from the card's "Ville (CP)".
    assert (b["external_id"], b["postal_code"], b["rent_cc"], b["furnished"]) == (
        "30089-552",
        "93200",
        1206,
        True,
    )

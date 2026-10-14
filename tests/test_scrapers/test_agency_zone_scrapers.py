"""URL building + postcode parsing for the zone-driven agency scrapers
(Orpi, Century 21, Laforêt, Nestenn, GLF, GTF). No network."""

from __future__ import annotations

import json
from html import escape
from urllib.parse import parse_qsl, urlsplit

import pytest
from bs4 import BeautifulSoup

from src.config import SearchCriteria
from src.scrapers import century21, glf, gtf, laforet, nestenn, orpi


@pytest.fixture
def criteria() -> SearchCriteria:
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
                    {
                        "code": "93210",
                        "city": "Saint-Denis",
                        "insee": "93066",
                        "areas": ["La Plaine Saint-Denis"],
                    },
                ]
            },
        }
    )


def _query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlsplit(url).query)


# ---- Orpi -------------------------------------------------------------------


def test_orpi_slug_candidates(criteria):
    rules = {r.code: r for r in criteria.zones.include}
    assert orpi.slug_candidates(rules["92300"]) == [
        "levallois-perret",
        "levallois-perret-hauts-de-seine",
    ]
    assert "saint-ouen" in orpi.slug_candidates(rules["93400"])
    assert "saint-denis-seine-saint-denis" in orpi.slug_candidates(rules["93200"])


def test_orpi_search_url(criteria):
    url = orpi.build_search_url(criteria, exclude={"saint-denis"})
    q = _query(url)
    slugs = [v for k, v in q if k.startswith("locations[")]
    assert "levallois-perret" in slugs
    assert "saint-denis" not in slugs
    # 93200 + 93210 share an INSEE code → one set of candidates only.
    assert slugs.count("saint-denis-seine-saint-denis") == 1
    assert ("maxPrice", "800") in q
    assert ("minSurface", "22") in q
    assert {v for k, v in q if k.startswith("nbRooms")} == {"1", "2"}
    assert ("transactions[0]", "rent") in q


def test_orpi_parse_results_ignores_similars_and_flags_misresolved():
    result = {
        "items": [
            {
                "slug": "appartement-t1-clichy-92110-7bd1d601-811d-4af1-a13f-d1444893bab0",
                "reference": "7bd1d601-811d-4af1-a13f-d1444893bab0",
                "tags": ["Studio", "Meublé"],
            }
        ],
        "count": 1,
        "similars": [{"slug": "appartement-t5-paris-75016-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}],
    }
    search = {
        "locations": [
            {"value": "clichy", "zipcode": ["92110"]},
            {"value": "saint-denis", "zipcode": ["97400"]},
            {"value": "bogus", "label": "bogus"},
        ]
    }
    html = (
        f'<div data-component="searchbar" data-search="{escape(json.dumps(search))}"></div>'
        f'<div data-component="estate-result" data-result="{escape(json.dumps(result))}"></div>'
    )
    items, locations, count = orpi.parse_search_results(html)
    assert count == 1 and len(items) == 1
    assert orpi.misresolved_slugs(locations, {"92110", "93200"}) == {"saint-denis"}
    entry = orpi.item_to_entry(items[0])
    assert entry["postal_code"] == "92110"
    assert entry["furnished"] == "1"
    assert entry["url"].endswith("-clichy-92110-7bd1d601-811d-4af1-a13f-d1444893bab0/")


def test_orpi_estate_to_listing():
    estate = {
        "zipCode": "92700",
        "price": 1150,
        "chargeReserve": 100,
        "surface": 37.23,
        "nbRooms": 2,
        "comfortFeatures": ["furnished"],
        "latitude": 48.9,
        "longitude": 2.23,
        "storyLocation": 2,
        "elevator": True,
        "seo": {"metaTitle": "Location appartement T-2 à Colombes"},
        "imagesFull": ["https://img/1.jpg"],
    }
    raw = orpi.estate_to_listing(estate, entry={"external_id": "x"}, url="https://o/x")
    assert raw.postal_code == "92700"
    assert raw.rent_cc == 1150 and raw.surface_m2 == 37.23
    assert raw.property_type == "T2" and raw.furnished is True and raw.floor == 2


def test_orpi_should_skip_furnished(criteria):
    s = orpi.OrpiScraper(session=None, criteria=criteria)
    assert not s._should_fetch_detail({"furnished": "1"})
    assert s._should_fetch_detail({})


# ---- Century 21 ---------------------------------------------------------------


def test_century21_pick_location_ids():
    sugg = [
        {"id": "v-la+plaine+st+denis", "cp": "93210", "name": "LA PLAINE ST DENIS"},
        {"id": "cpv-93210_st+denis", "cp": "93210", "name": "ST DENIS (93210)"},
        {"id": "v-autre", "cp": "93210", "name": "AUTRE"},
        {"id": "cpv-97400_st+denis", "cp": "97400", "name": "ST DENIS (97400)"},
    ]
    ids = century21.pick_location_ids(sugg, "93210", ["Saint-Denis", "La Plaine Saint-Denis"])
    assert ids == ["v-la+plaine+st+denis", "cpv-93210_st+denis"]


def test_century21_search_url(criteria):
    url = century21.build_search_url(
        ["v-levallois+perret", "cpv-93400_st+ouen", "v-clichy", "cpv-93200_st+denis"], criteria
    )
    assert url == (
        "https://www.century21.fr/annonces/f/location-appartement/"
        "v-clichy-levallois+perret/cpv-93200_st+denis-93400_st+ouen/s-22-/b-0-800/p-1-2/"
    )
    assert century21.build_search_url(["v-clichy"], criteria, page=2).endswith("/page-2/")


def test_century21_cards_skip_related():
    html = """
    <section class="js-the-list-of-properties-list">
      <div class="js-the-list-of-properties-list-property">
        <a href="/trouver_logement/detail/111/">COLOMBES 92</a></div>
    </section>
    <div class="js-the-list-of-properties-related">
      <div class="js-the-list-of-properties-related-property">
        <a href="/trouver_logement/detail/222/">GAGNY 93</a></div>
    </div>
    <a href="/annonces/f/location-appartement/v-clichy/page-2/">2</a>
    """
    entries, has_next = century21.parse_result_cards(html, page_num=1)
    assert [e["external_id"] for e in entries] == ["111"]
    assert entries[0]["url"] == "https://www.century21.fr/trouver_logement/detail/111/"
    assert has_next


def test_century21_postcode_from_detail_ignores_related_block():
    html = """
    <h1>Appartement F1 à louer 1 pièce - 27 m 2 COLOMBES  - 92</h1>
    <p>Exclusivité PARIS 75018 889 € par mois</p>
    <nav>Location Appartement COLOMBES (92700)</nav>
    """
    soup = BeautifulSoup(html, "lxml")
    assert century21.postcode_from_detail(soup) == "92700"
    assert century21.furnished_from_detail("Vue globale Location meublée") is True
    assert century21.furnished_from_detail("GRAND STUDIO NON MEUBLE") is False


# ---- Laforêt ----------------------------------------------------------------


def test_laforet_search_url_dedupes_insee(criteria):
    q = _query(laforet.build_search_url(criteria))
    assert [v for k, v in q if k == "filter[cities][]"] == ["92044", "92004", "93070", "93066"]
    assert ("filter[types][]", "apartment") in q
    assert ("filter[max]", "800") in q
    assert ("filter[surface]", "22") in q
    assert ("filter[features][is_not_furnished]", "1") in q


def test_laforet_cards_skip_nearest():
    def card(cid: str, city: str, pc: str) -> str:
        return (
            f'<article data-counter-id-value="{cid}">'
            f'<a href="https://www.laforet.com/agence-immobiliere/x/louer/{city}/appartement-{cid}'
            f'#section-video">x</a> Appartement 831 €/mois {city.upper()} ({pc})</article>'
        )

    html = (
        f'<div id="favoriz--page--1">{card("1", "bois-colombes", "92270")}</div>'
        f'<div id="standard--page--1">{card("2", "saint-denis", "93200")}</div>'
        f'<div id="nearest--page--1">{card("3", "stains", "93240")}</div>'
    )
    entries = laforet.parse_result_cards(html)
    assert [(e["external_id"], e["postal_code"]) for e in entries] == [
        ("1", "92270"),
        ("2", "93200"),
    ]
    assert "#" not in entries[0]["url"]


# ---- Nestenn ----------------------------------------------------------------


def test_nestenn_pick_villes():
    sugg = [
        {"id": "93210 Toutes les communes"},
        {"id": "93 Saint-denis"},
        {"id": "93210 LA PLAINE ST DENIS"},
        {"id": "97400 Saint-denis"},
    ]
    assert nestenn.pick_villes(sugg, "93210", ["Saint-Denis", "La Plaine Saint-Denis"]) == [
        "93 Saint-denis",
        "93210 LA PLAINE ST DENIS",
    ]
    assert nestenn.pick_villes([{"id": "93400 Saint-ouen"}], "93400", ["Saint-Ouen-sur-Seine"]) == [
        "93400 Saint-ouen"
    ]


def test_nestenn_search_url(criteria):
    q = _query(nestenn.build_search_url(["92300 Levallois-Perret", "93 Saint-denis"], criteria))
    assert [v for k, v in q if k == "villes[]"] == ["92300 Levallois-Perret", "93 Saint-denis"]
    assert ("typeTransaction", "louer") in q
    assert ("prix_max", "800") in q
    assert ("surface_min", "22") in q


def test_nestenn_cards():
    html = """
    <div id="listing_bien">
      <div class="bien_item"><a href="appartement-a-ref-1">x</a>
        EXCLU 93400 SAINT OUEN SUR SEINE Appartement loué 30m²</div>
      <div class="bien_item"><a href="appartement-b-ref-2">x</a>
        EXCLU 92110 CLICHY Appartement à louer 30m² 790 € CC/Mois</div>
      <div class="bien_item"><a href="appartement-c-ref-3">x</a>
        EXCLU Meublé 92600 ASNIERES Appartement à louer</div>
    </div>
    <div class="other"><div class="bien_item"><a href="appartement-d-ref-4">x</a>
      91300 MASSY</div></div>
    """
    entries = nestenn.parse_result_cards(html)
    assert [(e["external_id"], e.get("postal_code")) for e in entries] == [
        ("2", "92110"),
        ("3", "92600"),
    ]
    assert entries[0]["url"] == "https://nestenn.com/appartement-b-ref-2"
    assert entries[1]["furnished"] == "1" and "furnished" not in entries[0]


# ---- GLF / GTF ----------------------------------------------------------------


def test_glf_card_postcode():
    soup = BeautifulSoup(
        '<div class="card"><a href="/flats/1">voir</a> 92100 - Boulogne-Billancourt '
        "1 699€/mois</div>",
        "lxml",
    )
    assert glf.card_postcode(soup.select_one("a")) == "92100"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("NEUILLY SUR SEINE (92200) … 50 rue de Châteaudun 75009", "92200"),
        ("36 m² - Ref A-05073 SAINT-OUEN 93400", "93400"),
        ("Studio à Clichy 92110", "92110"),
    ],
)
def test_postcode_regex_any_french_postcode(text, expected):
    assert glf._POSTCODE_RE.search(text).group(1) == expected
    assert gtf._POSTCODE_RE.search(text).group(1) == expected


def test_gtf_slug_postcode():
    href = "/fr/bien-immobilier-neuilly-sur-seine-neuilly-sur-seine-92200-metro-sablons-3559"
    assert gtf._SLUG_POSTCODE_RE.search(href).group(1) == "92200"

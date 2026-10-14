"""Guard against scraper URL templates that break under str.format()."""

from __future__ import annotations

from src.scrapers.leboncoin import LBC_LISTING_URL
from src.scrapers.logic_immo import LOGIC_IMMO_URL
from src.scrapers.pap import PAP_LISTING_URL
from src.scrapers.seloger import SELOGER_LISTING_URL
from src.scrapers.superimmo import SUPERIMMO_LISTING_URL


def test_lbc_url_formats():
    url = LBC_LISTING_URL.format(
        max_price=800, rooms="1-2", min_surface=22, locations="Clichy_92110"
    )
    assert "locations=Clichy_92110" in url
    assert "square=22-max" in url


def test_seloger_url_formats():
    # Regression: the places=[{ci:…}] literal braces come in via the value.
    url = SELOGER_LISTING_URL.format(
        places="[{ci:920024}]", rooms="1,2", max_price=800, min_surface=22
    )
    assert "places=[{ci:920024}]" in url
    assert "max_price" not in url  # no raw placeholder leaked


def test_pap_url_formats():
    url = PAP_LISTING_URL.format(
        slug="clichy-92110", geo="g43269", filters="-jusqu-a-800-euros-a-partir-de-22-m2"
    )
    assert url.endswith(
        "locations-appartement-clichy-92110-g43269-jusqu-a-800-euros-a-partir-de-22-m2"
    )


def test_logic_immo_url_formats():
    url = LOGIC_IMMO_URL.format(arrondissements="01,02,11", max_price=2000, min_surface=40)
    assert "01,02,11" in url
    assert "2000eur" in url


def test_superimmo_url_formats():
    url = SUPERIMMO_LISTING_URL.format(
        region="ile-de-france",
        department="hauts-de-seine",
        town="clichy-92110",
        max_price=2000,
        min_surface=40,
    )
    assert "/hauts-de-seine/clichy-92110?" in url
    assert "prix-max=2000" in url
    assert "surface-min=40" in url

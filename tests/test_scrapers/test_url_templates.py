"""Guard against scraper URL templates that break under str.format()."""

from __future__ import annotations

from src.scrapers.leboncoin import LBC_LISTING_URL
from src.scrapers.logic_immo import LOGIC_IMMO_URL
from src.scrapers.pap import PAP_LISTING_URL
from src.scrapers.seloger import SELOGER_LISTING_URL
from src.scrapers.superimmo import SUPERIMMO_LISTING_URL


def test_lbc_url_formats():
    assert LBC_LISTING_URL.format(max_price=2000, locations="d_75")


def test_seloger_url_formats():
    # Regression: the places=[{ci:750056}] literal braces must be escaped.
    url = SELOGER_LISTING_URL.format(max_price=2000, min_surface=40)
    assert "[{ci:750056}]" in url
    assert "max_price" not in url  # no raw placeholder leaked


def test_pap_url_formats():
    url = PAP_LISTING_URL.format(max_price=2000, min_surface=40)
    assert "2000" in url
    assert "40" in url


def test_logic_immo_url_formats():
    url = LOGIC_IMMO_URL.format(arrondissements="01,02,11", max_price=2000, min_surface=40)
    assert "01,02,11" in url
    assert "2000eur" in url


def test_superimmo_url_formats():
    url = SUPERIMMO_LISTING_URL.format(max_price=2000, min_surface=40)
    assert "2000" in url
    assert "40" in url

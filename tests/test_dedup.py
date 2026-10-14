from types import SimpleNamespace

import pytest

from src.schemas.listing import RawListing
from src.storage.dedup import find_duplicate, is_cross_site_duplicate
from src.utils.hashing import dedup_key, normalise_text, title_fingerprint


def _listing(**overrides):
    base = {
        "source": "seloger",
        "external_id": "aaa",
        "url": "https://example.com/a",
        "title": "Bel appartement haussmannien",
        "rent_cc": 1800,
        "surface_m2": 50,
        "postal_code": "75011",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_title_fingerprint():
    assert title_fingerprint("Très Bel Appartement!") == "tres bel appartement"
    assert title_fingerprint("T3 haussmannien — 50 m²", n_words=2) == "t3 haussmannien"


def test_normalise_text():
    assert normalise_text("Cœur du 11ème !") == "coeur du 11eme"


def test_dedup_key_stable_across_small_price_variation():
    k1 = dedup_key(title="T3 lumineux", surface=50, rent_cc=1800, postal_code="75011")
    k2 = dedup_key(title="T3 lumineux", surface=50, rent_cc=1820, postal_code="75011")
    assert k1 == k2


def test_dedup_key_differs_on_zone():
    k1 = dedup_key(title="T3 lumineux", surface=50, rent_cc=1800, postal_code="75011")
    k2 = dedup_key(title="T3 lumineux", surface=50, rent_cc=1800, postal_code="75003")
    assert k1 != k2


def test_is_cross_site_duplicate_matches_close_variants():
    raw = RawListing(
        source="leboncoin",
        external_id="1",
        url="https://leboncoin.fr/x",
        title="Très bel appart haussmannien 3 pièces",
        rent_cc=1850,
        surface_m2=51,
        postal_code="75011",
    )
    candidate = _listing(
        title="Bel appartement haussmannien 3p",
        rent_cc=1820,
        surface_m2=50,
        postal_code="75011",
    )
    assert is_cross_site_duplicate(raw, candidate)


def test_is_cross_site_duplicate_rejects_same_source():
    raw = RawListing(
        source="seloger",
        external_id="1",
        url="https://seloger.fr/x",
        title="Bel appart haussmannien",
        rent_cc=1820,
        surface_m2=50,
        postal_code="75011",
    )
    candidate = _listing(source="seloger")
    assert not is_cross_site_duplicate(raw, candidate)


def test_is_cross_site_duplicate_rejects_mismatched_surface():
    raw = RawListing(
        source="leboncoin",
        external_id="1",
        url="https://leboncoin.fr/x",
        title="Bel appart haussmannien",
        rent_cc=1820,
        surface_m2=80,
        postal_code="75011",
    )
    candidate = _listing(surface_m2=50)
    assert not is_cross_site_duplicate(raw, candidate)


@pytest.mark.parametrize(
    "titles,expect_dup",
    [
        (("Coquet T3 cachet ancien", "Très coquet T3 ancien cachet"), True),
        (("Studio moderne avec balcon", "T3 haussmannien 4e étage"), False),
    ],
)
def test_find_duplicate(titles, expect_dup):
    raw = RawListing(
        source="leboncoin",
        external_id="1",
        url="https://leboncoin.fr/x",
        title=titles[0],
        rent_cc=1820,
        surface_m2=50,
        postal_code="75011",
    )
    candidate = _listing(title=titles[1])
    result = find_duplicate(raw, [candidate])
    assert (result is not None) == expect_dup

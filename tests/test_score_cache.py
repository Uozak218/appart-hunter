"""Test the content-hash scoring cache logic."""

from __future__ import annotations

from types import SimpleNamespace

from src.scoring.pipeline import _content_hash_for_listing


def _listing(**o):
    base = {
        "id": 1,
        "title": "T3 haussmannien",
        "description": "Parquet, moulures.",
        "rent_cc": 1850,
        "surface_m2": 52,
        "photos": [SimpleNamespace(url="https://img/1.jpg", position=0)],
    }
    base.update(o)
    return SimpleNamespace(**base)


def test_content_hash_is_stable():
    a = _content_hash_for_listing(_listing())
    b = _content_hash_for_listing(_listing())
    assert a == b
    assert len(a) == 40


def test_content_hash_changes_on_description_edit():
    a = _content_hash_for_listing(_listing())
    b = _content_hash_for_listing(_listing(description="Totally different text"))
    assert a != b


def test_content_hash_changes_on_new_photo():
    a = _content_hash_for_listing(_listing())
    b = _content_hash_for_listing(
        _listing(
            photos=[
                SimpleNamespace(url="https://img/1.jpg", position=0),
                SimpleNamespace(url="https://img/2.jpg", position=1),
            ]
        )
    )
    assert a != b


def test_content_hash_changes_on_price_drop():
    a = _content_hash_for_listing(_listing())
    b = _content_hash_for_listing(_listing(rent_cc=1750))
    assert a != b

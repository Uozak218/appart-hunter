"""SeLoger Next.js hydrated JSON parser regression test."""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.scrapers.seloger import _walk_for_seloger_items

FIXTURE = Path(__file__).parent.parent / "fixtures" / "seloger_listing.html"
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


def _load_next_data() -> dict:
    html = FIXTURE.read_text(encoding="utf-8")
    m = NEXT_DATA_RE.search(html)
    assert m is not None, "fixture must contain __NEXT_DATA__"
    return json.loads(m.group(1))


def test_walk_for_seloger_items_parses_both():
    data = _load_next_data()
    entries = _walk_for_seloger_items(data)
    ids = sorted(e["external_id"] for e in entries)
    assert ids == ["198765432", "198765433"]


def test_relative_permalinks_are_absolutised():
    data = _load_next_data()
    entries = _walk_for_seloger_items(data)
    urls = {e["external_id"]: e["url"] for e in entries}
    assert urls["198765432"].startswith("https://www.seloger.com/annonces/")
    assert urls["198765433"].startswith("https://www.seloger.com/annonces/")


def test_absolute_permalinks_preserved():
    data = _load_next_data()
    entries = _walk_for_seloger_items(data)
    urls = {e["external_id"]: e["url"] for e in entries}
    # The second listing was already an absolute URL
    assert (
        urls["198765433"]
        == "https://www.seloger.com/annonces/locations/appartement/paris-3/marais/198765433.htm"
    )

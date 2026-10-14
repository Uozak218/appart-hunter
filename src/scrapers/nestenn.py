"""Nestenn scraper.

Search: ``/listing?typeTransaction=louer&type_bien[]=Appartement&villes[]=…``
with ``prix_max`` / ``surface_min``. ``villes[]`` values must be the exact ids
of the site's autocomplete (``/autocomplete-ville/<q>``): "92300 Levallois-Perret",
"93400 Saint-ouen", "93 Saint-denis" (département-level id for homonyms)…
They're resolved once per process from ``criteria.zones.include`` (query by
postcode, keep ids whose name matches the city or one of its ``areas``).

Real results are the ``#listing_bien .bien_item`` cards; the block below
("Autres biens susceptibles de vous intéresser") is out-of-zone filler. Cards
show "NNNNN CITY" and "loué" for already-let flats, which we skip. Detail
anchors are **relative** slugs ending in ``-ref-<id>``; we absolutize them.

Validated against the live site on 2026-10-14.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import quote, urlencode, urljoin

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.config import SearchCriteria
from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

NESTENN_BASE = "https://nestenn.com"
NESTENN_SEARCH_URL = f"{NESTENN_BASE}/listing"
NESTENN_AUTOCOMPLETE_URL = f"{NESTENN_BASE}/autocomplete-ville/"
_DETAIL_RE = re.compile(r"([a-z0-9-]+-ref-(\d+))/?$", re.I)
_CARD_POSTCODE_RE = re.compile(r"\b(\d{5})\s+[A-Za-zÀ-ÿ]")
_LET_RE = re.compile(r"\b(?:lou[ée]|plus disponible)\b", re.I)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# postcode -> autocomplete ids, cached for the process lifetime.
_VILLE_CACHE: dict[str, list[str]] = {}


def _tokens(text: str) -> set[str]:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    norm = re.sub(r"\(.*?\)|\b\d+\b", " ", norm)
    toks = re.findall(r"[a-z]+", norm)
    return {"st" if t == "saint" else "ste" if t == "sainte" else t for t in toks}


def pick_villes(suggestions: list[dict[str, Any]], code: str, names: list[str]) -> list[str]:
    """Pick autocomplete ids ("<postcode|dept> <Name>") matching the town."""
    ids = [str(s.get("id") or "") for s in suggestions]
    ids = [i for i in ids if i and "toutes les communes" not in i.lower()]
    # Same postcode, or the département-level id used for homonyms ("93 Saint-denis").
    ids = [i for i in ids if i.split(" ", 1)[0] in (code, code[:2])]
    wanted = [_tokens(n) for n in names if _tokens(n)]
    matched = [i for i in ids if (c := _tokens(i)) and any(c <= w or w <= c for w in wanted)]
    return matched or [i for i in ids if i.startswith(code)]


async def resolve_villes(criteria: SearchCriteria) -> list[str]:
    villes: list[str] = []
    async with httpx.AsyncClient(headers=_HEADERS, timeout=20.0) as client:
        for rule in criteria.zones.include:
            if rule.code not in _VILLE_CACHE:
                try:
                    r = await client.get(NESTENN_AUTOCOMPLETE_URL + quote(rule.code))
                    r.raise_for_status()
                    _VILLE_CACHE[rule.code] = pick_villes(
                        r.json(), rule.code, [rule.city, *rule.areas]
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[nestenn] autocomplete failed for {rule.code}: {exc}")
                    continue
                if not _VILLE_CACHE[rule.code]:
                    logger.warning(f"[nestenn] no ville id for {rule.code} {rule.city}")
            for v in _VILLE_CACHE.get(rule.code, []):
                if v not in villes:
                    villes.append(v)
    return villes


def build_search_url(villes: list[str], criteria: SearchCriteria) -> str:
    params: list[tuple[str, str]] = [
        ("typeTransaction", "louer"),
        ("type_bien[]", "Appartement"),
    ]
    params += [("villes[]", v) for v in villes]
    params.append(("prix_max", str(int(criteria.property.max_rent_cc))))
    surfaces = list(criteria.property.min_surface.values())
    if surfaces:
        params.append(("surface_min", str(int(min(surfaces)))))
    return f"{NESTENN_SEARCH_URL}?{urlencode(params)}"


def parse_result_cards(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for card in soup.select("#listing_bien .bien_item"):
        a = card.select_one('a[href*="-ref-"]')
        m = _DETAIL_RE.search(a.get("href") or "") if a else None
        if not m or m.group(2) in seen:
            continue
        text = card.get_text(" ", strip=True)
        if _LET_RE.search(text):
            continue  # already let — dead ad
        seen.add(m.group(2))
        entry = {"external_id": m.group(2), "url": urljoin(NESTENN_BASE + "/", a["href"])}
        pc = _CARD_POSTCODE_RE.search(text)
        if pc:
            entry["postal_code"] = pc.group(1)
        if re.search(r"\bmeubl[ée]\b", text, re.I) and not re.search(r"non[- ]?meubl", text, re.I):
            entry["furnished"] = "1"
        entries.append(entry)
    return entries


class NestennScraper(BaseScraper):
    source = "nestenn"

    def _should_fetch_detail(self, entry: dict[str, Any]) -> bool:
        return not (self.criteria.property.furnished is False and entry.get("furnished"))

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        villes = await resolve_villes(self.criteria)
        if not villes:
            logger.warning("[nestenn] no ville resolved — nothing to search")
            return []
        url = build_search_url(villes, self.criteria)
        logger.info(f"[nestenn] fetching listing {url}")
        try:
            await page.goto(url, timeout=self.listing_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[nestenn] nav failed: {exc}")
            return []
        return parse_result_cards(await page.content())

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[nestenn] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        raw = parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=NESTENN_BASE
        )
        updates: dict[str, Any] = {"poster_type": "agency"}
        if not entry.get("postal_code"):
            h1 = soup.select_one("h1")
            scope = (h1.parent.get_text(" ", strip=True) if h1 and h1.parent else "") or ""
            pc = _CARD_POSTCODE_RE.search(scope)
            updates["postal_code"] = pc.group(1) if pc else None
        return raw.model_copy(update=updates)

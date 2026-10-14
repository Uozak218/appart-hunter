"""Locservice.fr scraper.

100% particulier-à-particulier — *le* gisement le plus prometteur pour
trouver une pépite sans frais d'agence + faible concurrence d'autres
candidats issus des grands agrégateurs.

Static HTML (HTTP-only), zéro anti-bot. Encodage utf-8.

Recherche : une requête par commune de ``criteria.zones.include`` via le
formulaire ``/recherche`` (clé = code INSEE), qui redirige vers la page ville
filtrée, ex. ``/seine-saint-denis-93/location-st-denis.html?types[]=appartement
&types[]=studio&criteres[]=non-meuble&pieces[]=1&pieces[]=2``. Pas de filtre
loyer/surface côté site : on les applique sur les cartes (qui portent ville +
code postal, surface et loyer) avant tout fetch de détail. La page ville
complète avec des annonces « À N km de … » d'autres communes : le code postal
de la carte permet au pré-filtre zone de les écarter.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import Page  # noqa: F401 — kept for signature compatibility

from src.config import SearchCriteria
from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.scrapers.bienici import max_rooms, passes_card_filters, unique_towns
from src.utils.logging import logger

LOCSERVICE_BASE = "https://www.locservice.fr"
LOCSERVICE_SEARCH_URL = LOCSERVICE_BASE + "/recherche"

# Detail URL examples:
#   /hauts-de-seine-92/location-appartement-levallois-perret/2385651
#   /seine-saint-denis-93/location-studio-st-denis/1541391
#   /seine-saint-denis-93/location-appartement-la-courneuve/p-cf-110318735964  (partenaire)
_DETAIL_RE = re.compile(
    r"/[a-z0-9-]+-(?:\d{2}|2[ab])/location-(appartement|maison|studio|chambre)-[a-z0-9-]+/"
    r"((?:p-[a-z]+-)?\d+)",
    re.I,
)
_CARD_POSTCODE_RE = re.compile(r"\((\d{5})\)")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def search_params(criteria: SearchCriteria, insee: str) -> list[tuple[str, str]]:
    """Query params for ``/recherche`` (the site's own search form)."""
    params: list[tuple[str, str]] = [
        ("search_ads[city][select]", insee),
        ("search_ads[context]", "rental"),
        ("search_ads[accommodationNatures][]", "studio"),
        ("search_ads[accommodationNatures][]", "flat"),
    ]
    furnished = criteria.property.furnished
    if furnished is False:
        params.append(("search_ads[criteria][]", "unfurnished"))
    elif furnished is True:
        params.append(("search_ads[criteria][]", "furnished"))
    rooms = max_rooms(criteria)
    if rooms:
        params += [("search_ads[roomsNumbers][]", str(n)) for n in range(1, min(rooms, 5) + 1)]
    return params


def _num(text: str) -> float | None:
    m = re.search(r"(\d[\d\s  ]*(?:[.,]\d+)?)", text or "")
    if not m:
        return None
    try:
        return float(re.sub(r"[\s  ]", "", m.group(1)).replace(",", "."))
    except ValueError:
        return None


def parse_cards(html: str) -> list[dict[str, Any]]:
    """Listing cards → entries (id, url, postcode, rent, surface, title)."""
    soup = BeautifulSoup(html, "lxml")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in soup.select("li.accommodation-ad"):
        a = card.select_one("h3 a[href], a.stretched-link[href]")
        href = (a.get("href") if a else "") or ""
        m = _DETAIL_RE.search(href)
        if not m:
            continue
        ad_id = m.group(2)
        if ad_id in seen:
            continue
        seen.add(ad_id)
        if href.startswith("/"):
            href = LOCSERVICE_BASE + href
        entry: dict[str, Any] = {
            "external_id": ad_id,
            "url": href,
            "title": a.get_text(" ", strip=True) if a else "",
            "nature": m.group(1).lower(),
            "partner": ad_id.startswith("p-"),
        }
        entry["furnished"] = bool(re.search(r"\bmeubl[ée]", entry["title"], re.I))
        for li in card.select(".accommodation-ad-characteristics li"):
            text = li.get_text(" ", strip=True)
            classes = " ".join(li.get("class") or [])
            if "price" in classes or "€" in text:
                entry["rent_cc"] = _num(text)
            elif "m²" in text:
                entry["surface_m2"] = _num(text)
            elif pc := _CARD_POSTCODE_RE.search(text):
                entry["postal_code"] = pc.group(1)
        entries.append(entry)
    return entries


class LocserviceScraper(BaseScraper):
    source = "locservice"
    use_http_only = True

    async def fetch_listing_page(self, page) -> list[dict[str, Any]]:  # noqa: ARG002
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True) as c:
            for rule in unique_towns(self.criteria):
                if not rule.insee:
                    logger.warning(f"[locservice] no INSEE code for {rule.code}; skipped")
                    continue
                try:
                    resp = await c.get(
                        LOCSERVICE_SEARCH_URL, params=search_params(self.criteria, rule.insee)
                    )
                    resp.raise_for_status()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[locservice] fetch failed for {rule.city}: {exc}")
                    continue
                final = str(resp.url)
                # An unknown INSEE falls back to a department/national page.
                if "/location-" not in final or "location-particulier" in final:
                    logger.warning(f"[locservice] {rule.city} ({rule.insee}) → {final}; skipped")
                    continue
                logger.info(f"[locservice] {rule.city}: {final}")
                for e in parse_cards(resp.text):
                    if e["external_id"] in seen:
                        continue
                    seen.add(e["external_id"])
                    entries.append(e)
        return entries

    def _should_fetch_detail(self, entry: dict[str, Any]) -> bool:
        """Card-level reject: room/house ads, furnished, price, surface."""
        if entry.get("nature") in ("chambre", "maison"):
            return False
        return passes_card_filters(self.criteria, entry)

    async def parse_detail(self, page, entry: dict[str, Any]) -> RawListing | None:  # noqa: ARG002
        url = entry["url"]
        try:
            async with httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True) as c:
                resp = await c.get(url)
                resp.raise_for_status()
                html = resp.text
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[locservice] detail fetch failed {url}: {exc}")
            return None
        soup = BeautifulSoup(html, "lxml")
        raw = parse_generic_detail(
            source=self.source, soup=soup, entry=entry, url=url, photo_base_url=LOCSERVICE_BASE
        )
        # The card values are structured and reliable; the generic body-text
        # regexes can latch onto unrelated amounts/surfaces.
        if entry.get("rent_cc"):
            raw.rent_cc = entry["rent_cc"]
        if entry.get("surface_m2"):
            raw.surface_m2 = entry["surface_m2"]
        if not raw.postal_code:
            m = _CARD_POSTCODE_RE.search(soup.get_text(" ", strip=True))
            raw.postal_code = m.group(1) if m else None
        if raw.rooms is None and entry.get("nature") == "studio":
            raw.rooms = 1
            raw.property_type = "T1"
        # Locservice annonces are private-individual posts (except "Logement
        # partenaire" cards) → tag poster_type so scoring/templates know it's a
        # particulier (no agency fee, faster to contact).
        raw.poster_type = None if entry.get("partner") else "particulier"
        return raw

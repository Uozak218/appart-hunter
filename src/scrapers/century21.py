"""Century 21 scraper.

Search URLs are path-encoded, built from ``criteria.zones.include``::

    /annonces/f/location-appartement/v-<city>-<city>/cpv-<cp>_<city>-.../
        s-<min_surface>-/b-0-<max_rent>/p-<rooms>-<rooms>/

Location ids (``v-levallois+perret``, ``cpv-93400_st+ouen``…) are not
derivable from the city name alone (homonyms get a ``cpv-<postcode>_`` id,
"Saint" becomes "st"), so we resolve them once per process through the site's
own autocomplete (``/autocomplete/localite/?q=<postcode>``) and keep the ids
whose name matches the configured city (or one of its ``areas``).

Filtered pages answer **HTTP 410** (noindex) but still render the results in
a real browser. The real results are the ``.js-the-list-of-properties-list-property``
cards; the carousel below them holds out-of-zone "related" suggestions.
Cards only show the département ("COLOMBES 92"); the full postcode comes from
the detail page breadcrumb ("COLOMBES (92700)").

Validated against the live site on 2026-10-14.
"""

from __future__ import annotations

import contextlib
import re
import unicodedata
from typing import Any

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.config import SearchCriteria
from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail, text_of
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

CENTURY21_BASE = "https://www.century21.fr"
CENTURY21_AUTOCOMPLETE_URL = f"{CENTURY21_BASE}/autocomplete/localite/"
_DETAIL_RE = re.compile(r"/trouver_logement/detail/(\d+)/?")
_RESULT_CARD = ".js-the-list-of-properties-list-property"
_MAX_PAGES = 3
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# postcode -> resolved location ids, cached for the process lifetime.
_LOCATION_CACHE: dict[str, list[str]] = {}


def _tokens(text: str) -> list[str]:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    norm = re.sub(r"\(.*?\)", " ", norm)
    toks = re.findall(r"[a-z0-9]+", norm)
    return ["st" if t == "saint" else "ste" if t == "sainte" else t for t in toks]


def _name_matches(candidate: str, names: list[str]) -> bool:
    cand = set(_tokens(candidate))
    if not cand:
        return False
    for name in names:
        want = set(_tokens(name))
        if want and (cand <= want or want <= cand):
            return True
    return False


def pick_location_ids(suggestions: list[dict[str, Any]], code: str, names: list[str]) -> list[str]:
    """Keep autocomplete ids for ``code`` whose name matches the town/areas."""
    same_cp = [s for s in suggestions if str(s.get("cp")) == code and s.get("id")]
    matched = [str(s["id"]) for s in same_cp if _name_matches(str(s.get("name", "")), names)]
    return matched or [str(s["id"]) for s in same_cp]


def build_search_url(location_ids: list[str], criteria: SearchCriteria, page: int = 1) -> str:
    """Mirror of the site's own ``_generateURL`` (theme/v6 search engine JS)."""
    cities = sorted({i[2:] for i in location_ids if i.startswith("v-")})
    cpvs = sorted({i[4:] for i in location_ids if i.startswith("cpv-")})
    cps = sorted({i[3:] for i in location_ids if i.startswith("cp-")})
    parts = ["location-appartement"]
    if cities:
        parts.append("v-" + "-".join(cities))
    if cps:
        parts.append("cp-" + "-".join(cps))
    if cpvs:
        parts.append("cpv-" + "-".join(cpvs))
    surfaces = list(criteria.property.min_surface.values())
    if surfaces:
        parts.append(f"s-{int(min(surfaces))}-")
    parts.append(f"b-0-{int(criteria.property.max_rent_cc)}")
    rooms = sorted(
        {
            int(m.group(1))
            for t in [*criteria.property.types.preferred, *criteria.property.types.acceptable]
            if (m := re.fullmatch(r"T(\d)", t.strip().upper()))
        }
    )
    if rooms:
        parts.append("p-" + "-".join(str(r) for r in rooms))
    if page > 1:
        parts.append(f"page-{page}")
    return f"{CENTURY21_BASE}/annonces/f/{'/'.join(parts)}/"


async def resolve_location_ids(criteria: SearchCriteria) -> list[str]:
    ids: list[str] = []
    async with httpx.AsyncClient(headers=_HEADERS, timeout=20.0) as client:
        for rule in criteria.zones.include:
            if rule.code not in _LOCATION_CACHE:
                try:
                    r = await client.get(CENTURY21_AUTOCOMPLETE_URL, params={"q": rule.code})
                    r.raise_for_status()
                    _LOCATION_CACHE[rule.code] = pick_location_ids(
                        r.json(), rule.code, [rule.city, *rule.areas]
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[century21] autocomplete failed for {rule.code}: {exc}")
                    continue
                if not _LOCATION_CACHE[rule.code]:
                    logger.warning(f"[century21] no location id for {rule.code} {rule.city}")
            for i in _LOCATION_CACHE.get(rule.code, []):
                if i not in ids:
                    ids.append(i)
    return ids


def parse_result_cards(html: str, page_num: int = 1) -> tuple[list[dict[str, str]], bool]:
    """Return (entries, has_next_page) from a search results page."""
    soup = BeautifulSoup(html, "lxml")
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for card in soup.select(_RESULT_CARD):
        a = card.select_one('a[href*="/trouver_logement/detail/"]')
        m = _DETAIL_RE.search(a.get("href") or "") if a else None
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        href = a["href"]
        if href.startswith("/"):
            href = CENTURY21_BASE + href
        # No furnished hint here: cards only carry a free-text snippet
        # ("GRAND STUDIO NON MEUBLE", "cuisine meublée"…) — decided on detail.
        entries.append({"external_id": m.group(1), "url": href})
    has_next = soup.select_one(f'a[href*="/page-{page_num + 1}/"]') is not None
    return entries, has_next


def postcode_from_detail(soup: BeautifulSoup) -> str | None:
    """Breadcrumb/heading carry "COLOMBES (92700)"; the h1 has the city name."""
    body = soup.get_text(" ", strip=True)
    h1 = text_of(soup.select_one("h1"))
    city_m = re.search(r"m\s*2\s+(.+?)\s+-\s+\d{2,3}\b", h1)
    if city_m:
        city = re.escape(city_m.group(1).strip()).replace(r"\ ", r"[\s-]+")
        m = re.search(rf"{city}\s*\((\d{{5}})\)", body, re.I)
        if m:
            return m.group(1)
    m = re.search(r"Location\s+Appartement\s+[^()]{2,60}\((\d{5})\)", body)
    return m.group(1) if m else None


def furnished_from_detail(body: str) -> bool | None:
    if re.search(r"\bnon[- ]meubl", body, re.I) or re.search(r"Location (?:vide|nue)", body):
        return False
    if re.search(r"Location meublée", body):
        return True
    return None


class Century21Scraper(BaseScraper):
    source = "century21"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        location_ids = await resolve_location_ids(self.criteria)
        if not location_ids:
            logger.warning("[century21] no location resolved — nothing to search")
            return []
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for page_num in range(1, _MAX_PAGES + 1):
            url = build_search_url(location_ids, self.criteria, page_num)
            logger.info(f"[century21] fetching listing {url}")
            try:
                # Filtered search pages answer 410 but render fine — ignore status.
                await page.goto(url, timeout=self.listing_timeout_ms, wait_until="domcontentloaded")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[century21] nav failed: {exc}")
                break
            html = await _settled_content(page)
            if html is None:
                break
            batch, has_next = parse_result_cards(html, page_num)
            new = [e for e in batch if e["external_id"] not in seen]
            for e in new:
                seen.add(e["external_id"])
            entries.extend(new)
            if not new or not has_next:
                break
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[century21] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        postcode = entry.get("postal_code") or postcode_from_detail(soup)
        # Pass the real postcode in, so the generic parser doesn't grab one
        # from the "related listings" block.
        hinted = {**entry, "postal_code": postcode} if postcode else entry
        raw = parse_generic_detail(
            source=self.source, soup=soup, entry=hinted, url=url, photo_base_url=CENTURY21_BASE
        )
        body = soup.get_text(" ", strip=True)
        updates: dict[str, Any] = {"poster_type": "agency"}
        if not postcode:
            updates["postal_code"] = None
        furnished = furnished_from_detail(body)
        if furnished is not None:
            updates["furnished"] = furnished
        if not raw.photo_urls:
            updates["photo_urls"] = _photos(soup, body)
        return raw.model_copy(update=updates)


async def _settled_content(page: Page) -> str | None:
    """Search pages re-navigate once client-side right after load; wait it out."""
    with contextlib.suppress(Exception):
        await page.wait_for_load_state("networkidle", timeout=10_000)
    for _ in range(3):
        try:
            return await page.content()
        except Exception:  # noqa: BLE001 — "page is navigating"
            await page.wait_for_timeout(1_500)
    logger.warning("[century21] page never settled")
    return None


def _photos(soup: BeautifulSoup, body: str) -> list[str]:
    """Own photos are ``/imagesBien/…_<ref>_…jpg``; related cards use other refs."""
    ref_m = re.search(r"Ref\s*:\s*(\d+)", body)
    ref = ref_m.group(1) if ref_m else None
    photos: list[str] = []
    for el in soup.select('[data-src*="/imagesBien/"], [src*="/imagesBien/"]'):
        src = el.get("data-src") or el.get("src") or ""
        if ref and f"_{ref}_" not in src:
            continue
        if src.startswith("/"):
            src = CENTURY21_BASE + src
        if src not in photos:
            photos.append(src)
    return photos[:15]

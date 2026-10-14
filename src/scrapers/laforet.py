"""Laforêt scraper.

Search: ``/louer/rechercher?filter[cities][]=<INSEE>&…`` — Laforêt keys its
city filter on the INSEE commune code, so the URL is built straight from
``criteria.zones.include`` (deduped by INSEE) plus apartment type, max rent,
min surface and the "non meublé" feature flag.

Server-rendered (Symfony UX live component). Real results are the
``<article data-counter-id-value>`` cards inside the ``#standard--page--N`` /
``#favoriz--page--N`` blocks; ``#nearest--page--N`` holds "nearby" suggestions
from other towns and is ignored. Each card shows "CITY (NNNNN)".

2026-10-14: the former Paris landing page
(``/departement/location-appartement-paris``) silently degraded into a
*national* list (Bordeaux, Saintes…) even though its <title> still says
"Paris (75)"; the old 75xxx card filter then dropped all 40 cards — that was
the "0 entries" seen in the smoke test (HTTP 200, no anti-bot). Moving to the
INSEE-keyed search fixes it.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.config import SearchCriteria
from src.schemas.listing import RawListing
from src.scrapers._common import parse_generic_detail
from src.scrapers.base import BaseScraper
from src.utils.logging import logger

LAFORET_BASE = "https://www.laforet.com"
LAFORET_SEARCH_URL = f"{LAFORET_BASE}/louer/rechercher"
# Real results only (document order); "nearest--page--N" = other-town filler.
_RESULT_CARDS = '[id^="standard--page--"] article, [id^="favoriz--page--"] article'
_CARD_POSTCODE_RE = re.compile(r"\((\d{5})\)")
_TOTAL_RE = re.compile(r"(\d+)\s+annonces?")


def build_search_url(criteria: SearchCriteria) -> str:
    params: list[tuple[str, str]] = []
    seen: set[str] = set()
    for rule in criteria.zones.include:
        if rule.insee and rule.insee not in seen:
            seen.add(rule.insee)
            params.append(("filter[cities][]", rule.insee))
    params.append(("filter[types][]", "apartment"))
    params.append(("filter[max]", str(int(criteria.property.max_rent_cc))))
    surfaces = list(criteria.property.min_surface.values())
    if surfaces:
        params.append(("filter[surface]", str(int(min(surfaces)))))
    if criteria.property.furnished is False:
        params.append(("filter[features][is_not_furnished]", "1"))
    elif criteria.property.furnished is True:
        params.append(("filter[features][is_furnished]", "1"))
    return f"{LAFORET_SEARCH_URL}?{urlencode(params)}"


def parse_result_cards(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for art in soup.select(_RESULT_CARDS):
        cid = art.get("data-counter-id-value")
        if not cid or cid in seen:
            continue
        link = art.select_one("a[href*='/louer/']")
        if not link:
            continue
        seen.add(cid)
        href = (link.get("href") or "").split("#", 1)[0]
        if href.startswith("/"):
            href = LAFORET_BASE + href
        entry = {"external_id": cid, "url": href}
        pc = _CARD_POSTCODE_RE.search(art.get_text(" ", strip=True))
        if pc:
            entry["postal_code"] = pc.group(1)
        entries.append(entry)
    return entries


class LaforetScraper(BaseScraper):
    source = "laforet"

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        url = build_search_url(self.criteria)
        logger.info(f"[laforet] fetching listing {url}")
        try:
            resp = await page.goto(
                url, timeout=self.listing_timeout_ms, wait_until="domcontentloaded"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[laforet] nav failed: {exc}")
            return []
        # A non-200 here is an anti-bot challenge (seen: HTTP 405 + captcha page),
        # not a genuinely empty listing. Flag it loudly so "0 entries" doesn't read
        # as "no ads" — and bail early since the challenge page has no cards.
        if resp is not None and resp.status != 200:
            logger.warning(
                f"[laforet] blocked: HTTP {resp.status} (likely anti-bot challenge) "
                f"— returning 0 entries"
            )
            return []
        html = await page.content()
        entries = parse_result_cards(html)
        title = BeautifulSoup(html, "lxml").title
        total_m = _TOTAL_RE.search(title.get_text() if title else "")
        if total_m and int(total_m.group(1)) > len(entries):
            logger.info(
                f"[laforet] {total_m.group(1)} results, only first {len(entries)} parsed (page 1)"
            )
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[laforet] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        postcode = entry.get("postal_code") or postcode_from_detail(soup)
        hinted = {**entry, "postal_code": postcode} if postcode else entry
        raw = parse_generic_detail(
            source=self.source, soup=soup, entry=hinted, url=url, photo_base_url=LAFORET_BASE
        )
        updates: dict[str, Any] = {"poster_type": "agency"}
        if not postcode:
            updates["postal_code"] = None  # never trust the generic body-text fallback
        return raw.model_copy(update=updates)


def postcode_from_detail(soup: BeautifulSoup) -> str | None:
    """Header shows "Appartement T1 BOIS COLOMBES à louer BOIS COLOMBES (92270)"."""
    h1 = soup.select_one("h1")
    scope = h1.parent.get_text(" ", strip=True) if h1 and h1.parent else ""
    m = _CARD_POSTCODE_RE.search(scope) or _CARD_POSTCODE_RE.search(soup.get_text(" ", strip=True))
    return m.group(1) if m else None

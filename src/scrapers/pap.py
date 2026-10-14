"""PAP scraper — skeleton.

PAP historically has relatively lenient anti-bot. Full implementation TODO:
parse `.search-results-list-item` cards on the listing page and the detail
page's structured-data JSON.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.config import SearchCriteria
from src.schemas.listing import RawListing
from src.scrapers.base import BaseScraper
from src.utils.hashing import normalise_text
from src.utils.logging import logger

# PAP search URL: ``/annonce/locations-appartement-<slug>-g<id>[g<id>…]-<filters>``.
# Several towns are combined by concatenating their geo ids after the first
# town's slug (PAP's own URLs do this, e.g. ``…-paris-75-g439g471g37774…``);
# the slug is cosmetic, the ``g`` ids drive the search.
# Filters (from PAP's own SEO URLs): ``studio`` / ``2-pieces`` /
# ``du-studio-au-2-pieces``, ``jusqu-a-800-euros``, ``a-partir-de-22-m2``.
# PAP has no "non meublé" URL filter (only ``-meuble``) — the scorer/hard
# filter drops furnished ads.
PAP_LISTING_URL = "https://www.pap.fr/annonce/locations-appartement-{slug}-{geo}{filters}"

# PAP geo ids, keyed by INSEE commune code. PAP's geo autocomplete sits behind
# Cloudflare, so these were read off PAP's public search-page URLs
# (``/annonce/locations-<town>-<postcode>-g<id>``) in Oct 2026. Add a line
# here when a new town is added to ``zones.include``; unknown towns are
# skipped with a warning.
PAP_GEO_IDS: dict[str, int] = {
    "92044": 43289,  # Levallois-Perret (92300)
    "92024": 43269,  # Clichy (92110)
    "92004": 43299,  # Asnières-sur-Seine (92600)
    "92009": 43276,  # Bois-Colombes (92270)
    "92035": 43280,  # La Garenne-Colombes (92250)
    "92025": 43300,  # Colombes (92700)
    "93070": 43335,  # Saint-Ouen-sur-Seine (93400)
    "93066": 43705,  # Saint-Denis (93200 + 93210, one PAP geo: 'saint-denis-93')
    "75056": 439,  # Paris (75)
}

# '/annonces/appartement-clichy-92110-r439202345' → '92110'.
_PAP_URL_POSTCODE_RE = re.compile(r"-((?:0[1-9]|[1-8]\d|9[0-5])\d{3})-r\d+")
_POSTCODE_PAREN_RE = re.compile(r"\(((?:0[1-9]|[1-8]\d|9[0-5])\d{3})\)")
_POSTCODE_RE = re.compile(r"\b((?:0[1-9]|[1-8]\d|9[0-5])\d{3})\b")


def _rooms_token(c: SearchCriteria) -> str:
    counts: set[int] = set()
    for t in c.property.types.preferred + c.property.types.acceptable:
        m = re.fullmatch(r"[TtFf](\d+)", t.strip())
        if m:
            counts.add(int(m.group(1)))
        elif t.strip().lower() == "studio":
            counts.add(1)
    if not counts:
        return ""

    def _one(n: int) -> str:
        return "studio" if n == 1 else f"{n}-pieces"

    lo, hi = min(counts), max(counts)
    return _one(lo) if lo == hi else f"du-{_one(lo)}-au-{_one(hi)}"


def _min_surface(c: SearchCriteria) -> int:
    wanted = c.property.types.preferred + c.property.types.acceptable
    floors = [v for k, v in c.property.min_surface.items() if k in wanted]
    floors = floors or list(c.property.min_surface.values())
    return int(min(floors)) if floors else 0


def build_listing_url(c: SearchCriteria) -> str | None:
    """One PAP search URL covering every ``zones.include`` town (deduped by INSEE)."""
    ids: list[int] = []
    first_slug = ""
    for z in c.zones.include:
        gid = PAP_GEO_IDS.get(z.insee)
        if gid is None:
            logger.warning(f"[pap] no PAP geo id for {z.city!r} (INSEE {z.insee!r}) — skipped")
            continue
        if gid in ids:
            continue
        if not ids:
            first_slug = "-".join(filter(None, [normalise_text(z.city).replace(" ", "-"), z.code]))
        ids.append(gid)
    if not ids:
        return None
    parts = [
        _rooms_token(c),
        f"jusqu-a-{int(c.property.max_rent_cc)}-euros",
    ]
    min_surface = _min_surface(c)
    if min_surface:
        parts.append(f"a-partir-de-{min_surface}-m2")
    filters = "".join(f"-{p}" for p in parts if p)
    return PAP_LISTING_URL.format(
        slug=first_slug, geo="".join(f"g{i}" for i in ids), filters=filters
    )


def postcode_from_pap_url(url: str) -> str | None:
    m = _PAP_URL_POSTCODE_RE.search(url or "")
    return m.group(1) if m else None


def extract_postcode(*texts: str, url: str = "") -> str | None:
    """'Clichy (92110)' in title/summary → URL slug → first 5-digit code."""
    joined = " ".join(t for t in texts if t)
    m = _POSTCODE_PAREN_RE.search(joined)
    if m:
        return m.group(1)
    from_url = postcode_from_pap_url(url)
    if from_url:
        return from_url
    m = _POSTCODE_RE.search(joined)
    return m.group(1) if m else None


class PapScraper(BaseScraper):
    source = "pap"
    # Cloudflare Turnstile — Scrapfly's ASP solves the JS challenge.
    # PAP renders server-side once unblocked, so render_js stays off (saves ~5× credits).
    use_scrapfly = True
    scrapfly_render_js = False

    async def fetch_listing_page(self, page: Page) -> list[dict[str, str]]:
        url = build_listing_url(self.criteria)
        if url is None:
            logger.warning("[pap] no zone with a known PAP geo id — nothing to fetch")
            return []
        logger.info(f"[pap] fetching listing {url}")
        await page.goto(url, timeout=self.listing_timeout_ms, wait_until="domcontentloaded")
        soup = BeautifulSoup(await page.content(), "lxml")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in soup.select("a.item-title, a[href*='/annonces/']"):
            href = a.get("href")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.pap.fr" + href
            m = re.search(r"/([a-z0-9-]+-r\d+)/?", href)
            if not m:
                continue
            ext = m.group(1)
            if ext in seen:
                continue
            seen.add(ext)
            entry = {"external_id": ext, "url": href}
            postcode = postcode_from_pap_url(href)
            if postcode:
                entry["postal_code"] = postcode
            entries.append(entry)
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, str]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[pap] detail nav failed {url}: {exc}")
            return None
        soup = BeautifulSoup(await page.content(), "lxml")
        title = (soup.select_one("h1") or soup.new_tag("h1")).get_text(strip=True)
        description = _text(soup.select_one(".item-description, .annonce-description"))
        price_text = _text(soup.select_one(".item-price, .price"))
        surface_text = _text(soup.select_one(".item-summary"))

        rent = _parse_price(price_text)
        surface = _parse_surface(surface_text)
        rooms = _rooms(title)
        postcode = extract_postcode(title, surface_text, url=url)
        photos = [
            img.get("data-src") or img.get("src")
            for img in soup.select(".carousel img, .owl-item img")[:15]
            if img.get("data-src") or img.get("src")
        ]
        return RawListing(
            source=self.source,
            external_id=entry["external_id"],
            url=url,
            title=title,
            description=description,
            rent_cc=rent,
            surface_m2=surface,
            rooms=rooms,
            property_type=f"T{rooms}" if rooms else None,
            postal_code=postcode,
            photo_urls=[p for p in photos if p],
        )


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _parse_price(text: str) -> float | None:
    m = re.search(r"([\d\s\u00a0.,]+)\s*€", text or "")
    if not m:
        return None
    cleaned = re.sub(r"[^\d,.]", "", m.group(1)).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_surface(text: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m", text or "")
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def _rooms(title: str) -> int | None:
    m = re.search(r"\bT(\d)\b", title or "", re.I)
    return int(m.group(1)) if m else None

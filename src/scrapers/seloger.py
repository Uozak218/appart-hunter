"""SeLoger scraper (skeleton to flesh out once CSS selectors are validated).

SeLoger sits behind Cloudflare. Like LBC we rely on the hydrated Next/Nuxt
JSON blob embedded in the page. The CSS selectors here are best-guess
fallbacks — re-check them the first time you run against prod and tune in
``parse_detail``. When the JSON path breaks, the scraper raises, the health
tracker flags it, and the notifier alerts "scraper seloger down".
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any

from bs4 import BeautifulSoup
from playwright.async_api import Page

from src.config import SearchCriteria, get_criteria
from src.schemas.listing import RawListing
from src.scrapers.base import BaseScraper
from src.utils.geo import is_in_target_zone
from src.utils.hashing import normalise_text
from src.utils.logging import logger

# Note: SeLoger's ``places`` parameter uses literal braces (``[{ci:920024}]``)
# that clash with Python str.format — the value is injected whole via
# ``{places}``, so no brace escaping is needed in the template.
# types=1 → appartement only. rooms=1,2 → studio + 2 pièces.
SELOGER_LISTING_URL = (
    "https://www.seloger.com/list.htm?"
    "projects=1&types=1&places={places}&rooms={rooms}&price=NaN/{max_price}&"
    "surface={min_surface}/NaN&enterprise=0&natures=1,2,4&"
    "sort=d_dt_crea&qsVersion=1.0"
)

_NUXT_RE = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# SeLoger detail URLs carry the commune slug + département:
#   '.../annonces/locations/appartement/clichy-92/centre-ville/123.htm'
#   '.../annonces/locations/appartement/paris-11eme-75/123.htm'
_SELOGER_LOC_RE = re.compile(r"/annonces/locations/[^/]+/([a-z0-9-]+?)-(\d{2}|2[ab])/", re.I)
_PARIS_ARR_RE = re.compile(r"^paris-(\d{1,2})(?:er|eme|e)?$")
# "Clichy (92110)" / "92110 Clichy" — any metropolitan 5-digit postcode.
_POSTCODE_RE = re.compile(r"\b((?:0[1-9]|[1-8]\d|9[0-5])\d{3})\b")
_CITY_POSTCODE_RE = re.compile(r"\(((?:0[1-9]|[1-8]\d|9[0-5])\d{3})\)")


def seloger_ci(insee: str) -> str | None:
    """INSEE commune code → SeLoger ``ci`` id: '92024' → '920024'.

    SeLoger pads the 3-digit commune suffix with a 0 after the 2-char
    département (Paris 75056 → 750056, Saint-Ouen 93070 → 930070, as in its
    ``/prix-de-l-immo/.../saint-ouen/930070.htm`` pages).
    """
    insee = (insee or "").strip()
    if len(insee) != 5:
        return None
    return f"{insee[:2]}0{insee[2:]}"


def _seloger_places(c: SearchCriteria) -> str:
    """``zones.include`` → ``[{ci:920044}|{ci:920024}|…]`` (deduped by INSEE)."""
    cis: list[str] = []
    for z in c.zones.include:
        ci = seloger_ci(z.insee)
        if ci is None:
            logger.warning(f"[seloger] zone {z.code} {z.city!r} has no INSEE code — skipped")
            continue
        if ci not in cis:
            cis.append(ci)
    return "[" + "|".join(f"{{ci:{ci}}}" for ci in cis) + "]"


def _wanted_room_counts(c: SearchCriteria) -> list[int]:
    counts: set[int] = set()
    for t in c.property.types.preferred + c.property.types.acceptable:
        m = re.fullmatch(r"[TtFf](\d+)", t.strip())
        if m:
            counts.add(int(m.group(1)))
        elif t.strip().lower() == "studio":
            counts.add(1)
    return sorted(counts)


def _min_surface(c: SearchCriteria) -> int:
    wanted = c.property.types.preferred + c.property.types.acceptable
    floors = [v for k, v in c.property.min_surface.items() if k in wanted]
    floors = floors or list(c.property.min_surface.values())
    return int(min(floors)) if floors else 0


def build_listing_url(criteria: SearchCriteria | None = None) -> str:
    c = criteria or get_criteria()
    rooms = _wanted_room_counts(c)
    return SELOGER_LISTING_URL.format(
        places=_seloger_places(c),
        rooms=",".join(str(r) for r in rooms) if rooms else "NaN",
        max_price=int(c.property.max_rent_cc),
        min_surface=_min_surface(c) or "NaN",
    )


def _slug(text: str) -> str:
    return normalise_text(text).replace(" ", "-")


def seloger_url_location(url: str) -> tuple[str, str] | None:
    """'/…/appartement/clichy-92/centre/1.htm' → ('clichy', '92'). None if absent."""
    m = _SELOGER_LOC_RE.search(url or "")
    if not m:
        return None
    return m.group(1).lower(), m.group(2).upper()


def postcode_from_seloger_url(url: str, criteria: SearchCriteria | None = None) -> str | None:
    """Best-effort postcode from a SeLoger detail URL.

    - Paris arrondissement slug: 'paris-11eme-75' → '75011'.
    - Commune slug matching a ``zones.include`` town with a single postcode:
      'clichy-92' → '92110'. Towns with several postcodes (Saint-Denis
      93200/93210) are ambiguous → None.
    """
    loc = seloger_url_location(url)
    if loc is None:
        return None
    slug, dept = loc
    m = _PARIS_ARR_RE.match(slug)
    if m and dept == "75":
        arr = int(m.group(1))
        return f"750{arr:02d}" if 1 <= arr <= 20 else None
    if criteria is None:
        return None
    codes = {
        z.code for z in criteria.zones.include if _slug(z.city) == slug and z.code.startswith(dept)
    }
    return codes.pop() if len(codes) == 1 else None


def url_is_out_of_zone(url: str, criteria: SearchCriteria) -> bool:
    """True when the URL provably points outside ``zones.include``.

    Conservative: unparseable URL or ambiguous town → False (keep).
    """
    loc = seloger_url_location(url)
    if loc is None:
        return False
    slug, dept = loc
    postcode = postcode_from_seloger_url(url, criteria)
    if postcode is not None:
        return not is_in_target_zone(criteria, postcode=postcode, lat=None, lon=None)
    # Town not resolved to a single postcode: out of zone unless it names one
    # of the target communes (e.g. Saint-Denis with two postcodes).
    known = {(_slug(z.city), z.code[:2]) for z in criteria.zones.include if z.city}
    return (slug, dept) not in known


class SeLogerScraper(BaseScraper):
    source = "seloger"
    # DataDome — fetch via Scrapfly. SeLoger hydrates results client-side.
    use_scrapfly = True
    scrapfly_render_js = True

    def _should_fetch_detail(self, entry: dict[str, Any]) -> bool:
        # SeLoger's listing page only gives id + url, so the only thing we can
        # reject before paying for the detail is the zone — and the commune
        # (or Paris arrondissement) is in the URL slug.
        url = entry.get("url", "")
        if url_is_out_of_zone(url, self.criteria):
            logger.debug(f"[seloger] skip detail {entry.get('external_id')}: hors zone ({url})")
            return False
        return True

    async def fetch_listing_page(self, page: Page) -> list[dict[str, Any]]:
        url = build_listing_url(self.criteria)
        logger.info(f"[seloger] fetching listing {url}")
        await page.goto(url, timeout=self.listing_timeout_ms, wait_until="domcontentloaded")

        # Cloudflare interstitial? give it a sec.
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=8_000)

        html = await page.content()
        m = _NUXT_RE.search(html)
        if m:
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                data = None
            entries = _walk_for_seloger_items(data) if data else []
            if entries:
                return entries

        # HTML fallback — SeLoger no longer exposes __NEXT_DATA__ (2026 redesign).
        # Detail URLs: /annonces/locations/appartement/<commune>-<dept>/[quartier/]NNN.htm
        soup = BeautifulSoup(html, "lxml")
        entries = []
        seen: set[str] = set()
        for a in soup.select("a[href*='/annonces/locations/']"):
            href = a.get("href") or ""
            ext = _extract_id_from_url(href)
            if not ext or ext in seen:
                continue
            seen.add(ext)
            # Strip query string + relative-resolve.
            href = href.split("?", 1)[0]
            if href.startswith("/"):
                href = "https://www.seloger.com" + href
            entry: dict[str, Any] = {"external_id": str(ext), "url": href}
            postcode = postcode_from_seloger_url(href, self.criteria)
            if postcode:
                entry["postal_code"] = postcode
            entries.append(entry)
        logger.debug(f"[seloger] parsed {len(entries)} entries from HTML")
        return entries

    async def parse_detail(self, page: Page, entry: dict[str, Any]) -> RawListing | None:
        url = entry["url"]
        try:
            await page.goto(url, timeout=self.detail_timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[seloger] detail nav failed {url}: {exc}")
            return None

        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=8_000)
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)

        rent = _extract_rent(text)
        surface = _extract_surface(text)
        rooms = _extract_rooms(text)

        postcode = extract_postcode(text, url, self.criteria)
        city = _city_for_postcode(self.criteria, postcode)

        page_title = soup.find("title")
        title = (
            page_title.get_text(strip=True).split(" - SeLoger")[0].strip() if page_title else ""
        ) or f"Appartement {('T' + str(rooms) + ' ') if rooms else ''}{city or ''}".strip()
        description = _text(soup.select_one("[class*='escription'], [data-test*='escription']"))

        photos: list[str] = []
        for img in soup.select("img[src], img[data-src]"):
            src = (img.get("src") or img.get("data-src") or "").strip()
            if src.startswith("http") and any(k in src for k in ("seloger", "/photos", "cdn")):
                photos.append(src)
            if len(photos) >= 20:
                break

        return RawListing(
            source=self.source,
            external_id=str(entry["external_id"]),
            url=url,
            title=title,
            description=description,
            rent_cc=rent,
            surface_m2=surface,
            rooms=rooms,
            property_type=f"T{rooms}" if rooms else None,
            postal_code=postcode,
            photo_urls=photos,
            raw={"html_length": len(html)},
        )


def extract_postcode(
    text: str, url: str = "", criteria: SearchCriteria | None = None
) -> str | None:
    """Postcode of a detail page — any French 5-digit code, not just 75xxx.

    Order: 'Ville (92110)' in the page text → URL slug → first target-zone
    postcode seen in the text → first plausible postcode in the text.
    """
    m = _CITY_POSTCODE_RE.search(text or "")
    if m:
        return m.group(1)
    from_url = postcode_from_seloger_url(url, criteria)
    if from_url:
        return from_url
    candidates = _POSTCODE_RE.findall(text or "")
    if criteria is not None:
        targets = {z.code for z in criteria.zones.include}
        for pc in candidates:
            if pc in targets:
                return pc
    return candidates[0] if candidates else None


def _city_for_postcode(criteria: SearchCriteria | None, postcode: str | None) -> str | None:
    if criteria is None or not postcode:
        return None
    for z in criteria.zones.include:
        if z.code == postcode and z.city:
            return z.city
    return None


def _walk_for_seloger_items(data: Any) -> list[dict[str, Any]]:
    """Best-effort: look for items with `idAnnonce` and `permalink` fields."""
    found: list[dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            pid = node.get("idAnnonce") or node.get("id")
            perma = node.get("permalink") or node.get("url")
            if pid and perma and isinstance(perma, str) and "/annonces/" in perma:
                href = perma if perma.startswith("http") else f"https://www.seloger.com{perma}"
                found.append({"external_id": str(pid), "url": href})
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(data)
    return found


def _text(node) -> str:
    return node.get_text(strip=True) if node else ""


def _parse_price(text: str) -> float | None:
    """Parse a French-formatted euro price (point=thousands, comma=decimal)."""
    if not text:
        return None
    m = re.search(r"([\d\s\u00a0.,]+)\s*€", text)
    if not m:
        return None
    raw = re.sub(r"[\s\u00a0]", "", m.group(1))
    if "," in raw:
        cleaned = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        cleaned = raw.replace(".", "")
    else:
        cleaned = raw
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_surface(text: str) -> float | None:
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m", text)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


# SeLoger's 2026 redesign dropped stable CSS hooks for price/surface/rooms, so we
# pull them from the rendered page text instead, anchored on rent-specific markers.
def _extract_rent(text: str) -> float | None:
    m = re.search(r"([\d][\d\s .,]{0,12}?)\s*€\s*/\s*mois", text)
    if m is None:
        m = re.search(r"([\d][\d\s .,]{0,12}?)\s*€\s*(?:CC|charges)", text)
    return _parse_price(f"{m.group(1)} €") if m else None


def _extract_surface(text: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m[²2]", text)
    return float(m.group(1).replace(",", ".")) if m else None


def _extract_rooms(text: str) -> int | None:
    m = re.search(r"\bT(\d)\b", text) or re.search(r"(\d+)\s*pi[eè]ce", text)
    return int(m.group(1)) if m else None


def _extract_id_from_url(url: str) -> str | None:
    m = re.search(r"/(\d{7,})/?", url)
    return m.group(1) if m else None

"""Shared HTML parsing helpers for skeleton scrapers.

Used by the niche agency scrapers whose detail pages share a near-identical
structure (h1 title + price/surface inferable from body text). Each scraper
is still free to override per-site quirks.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from src.schemas.listing import RawListing


def text_of(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def parse_price(text: str) -> float | None:
    """Parse a French-formatted euro price (point=thousands, comma=decimal)."""
    m = re.search(r"([\d\s .,]+)\s*€", text or "")
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


def parse_surface(text: str) -> float | None:
    """Match strictly ``N m²`` / ``N m2`` / ``N m ²``.

    Previous loose pattern ``\\d+\\s*m`` also captured ``12 m du métro``
    (distance-to-transport) → bogus 12 m² surface that fails the prefilter
    on real T2/T3 apartments. We now require an explicit square-meter mark.
    """
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m\s*(?:²|2|\\u00b2)\b", text)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def parse_generic_detail(
    *, source: str, soup: BeautifulSoup, entry: dict[str, str], url: str, photo_base_url: str = ""
) -> RawListing:
    title = text_of(soup.select_one("h1"))
    description = text_of(
        soup.select_one(".description, [itemprop='description'], .annonce-description")
    )
    price_text = text_of(soup.select_one("[class*='rice'], [class*='rix'], .price"))
    body_text = soup.get_text(" ", strip=True)
    rent = parse_price(price_text) or parse_price(body_text)
    surface = parse_surface(body_text)
    rooms_src = title + " " + body_text
    rooms_match = re.search(r"\bT\s?(\d)\b", rooms_src, re.I) or re.search(
        r"(\d)\s*pi[èe]ces?", rooms_src, re.I
    )
    rooms = int(rooms_match.group(1)) if rooms_match else None
    if rooms is None and re.search(r"\bstudio\b", title, re.I):
        rooms = 1
    # Trust the listing-page hint (URL slug, etc.) over body text. The body
    # often mentions the agency's own address (different postcode) and shadows
    # the real one — happened on Orpi where /paris-10-75010-uuid/ listings had
    # an agence Paris 8e footer that hijacked the match.
    entry_pc = entry.get("postal_code")
    if entry_pc:
        postcode = str(entry_pc)
    else:
        m = re.search(r"\b((?:75|77|78|9[1-5])\d{3})\b", body_text)
        postcode = m.group(1) if m else None
    photos: list[str] = []
    seen_photos: set[str] = set()

    # Banner / logo / placeholder patterns we never want to pass to Claude —
    # they pollute the visual scoring with brand chrome and confidence-eroding
    # filler images.
    _NOISE_RE = re.compile(
        r"(\.svg(?:$|\?)|logo|banner|placeholder|"
        r"theme/v\d+/|the_ad-|estimation|/static/|/img/|"
        r"immobilier[^/]*confiance|favicon|sprite)",
        re.I,
    )

    def _add_photo(src: str | None) -> None:
        if not src or not src.strip():
            return
        s = src.strip()
        if s.startswith("//"):
            s = "https:" + s
        elif s.startswith("/") and photo_base_url:
            s = photo_base_url + s
        if not s.startswith("http") or s in seen_photos:
            return
        if _NOISE_RE.search(s):
            return
        seen_photos.add(s)
        photos.append(s)

    # Special case: Nestenn (and clones) expose all photo URLs in a single
    # ``data-photos`` attribute on ``div.photo_bien``, comma-separated.
    bulk_node = soup.select_one("[data-photos]")
    if bulk_node:
        for u in (bulk_node.get("data-photos") or "").split(","):
            _add_photo(u)

    # Generic CSS — extended to cover orpi (`c-swiper`), laforet (`keen-slider`,
    # alt='Photo n°…'), bienici/seloger/etc.
    photo_selector = (
        ".carousel img, .gallery img, picture img, .slider img, "
        "[class*='hoto'] img, [class*='swiper'] img, [class*='slider'] img, "
        "img[alt^='Photo'], img.photo, "
        # Engel & Völkers exposes photos via data-testid="property-image-N"
        "img[data-testid*='property-image'], "
        # Barnes uses a custom class on real photos
        "img.bien_slide_patch"
    )
    for img in soup.select(photo_selector)[:30]:
        _add_photo(img.get("data-src") or img.get("src"))

    # Breteuil (Next.js) lists photos as <link rel="preload" as="image"> in <head>.
    for link in soup.select("link[rel='preload'][as='image'][href]")[:30]:
        href = link.get("href") or ""
        # Only keep CDN-hosted photos (avoid preload of UI icons / banners).
        if "properties-photos" in href or "cdn.bre.im" in href:
            _add_photo(href)

    # Paris-Seine renders photos as CSS background-image on <div>s (no <img>).
    bg_re = re.compile(r"background-image\s*:\s*url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.I)
    for el in soup.select("[style*='background-image']")[:30]:
        style = el.get("style") or ""
        m = bg_re.search(style)
        if m:
            _add_photo(m.group(1))

    # Last-resort fallback: scan all <img> srcs for known photo-CDN hostnames.
    # Useful for sites whose <img> aren't inside any of the matched containers
    # (e.g. Next.js-hashed classnames). Keeps false positives low by limiting
    # to known image-CDN patterns.
    if not photos:
        cdn_pat = re.compile(
            r"(?:media\.apimo\.pro|photo\.barnes-international\.com|"
            r"uploadcare\.engelvoelkers\.com|cdn\.bre\.im|staticlbi\.com|"
            r"cloudimg\.io|cloudfront\.net|paris-seine-immobilier\.com|"
            r"immo-facile\.com)",
            re.I,
        )
        for img in soup.select("img[src], img[data-src]")[:40]:
            src = img.get("data-src") or img.get("src") or ""
            if cdn_pat.search(src):
                _add_photo(src)

    photos = photos[:15]
    return RawListing(
        source=source,
        external_id=entry["external_id"],
        url=url,
        title=title,
        description=description,
        rent_cc=rent,
        surface_m2=surface,
        rooms=rooms,
        property_type=f"T{rooms}" if rooms else None,
        postal_code=str(postcode) if postcode else None,
        photo_urls=photos,
    )

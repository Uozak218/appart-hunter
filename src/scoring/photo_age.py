"""Detect the age of listing photos from EXIF metadata.

Most photo CDNs strip EXIF (LBC / SeLoger / Bien'ici do), but enough small
hosts and direct-upload flows preserve ``DateTimeOriginal`` that this still
catches a meaningful chunk of recycled listings. When the newest photo is
older than 18 months, we treat the listing as suspicious (re-posted, scam,
or landlord slow to update).

The detector is best-effort and silent on failure: if no photo exposes EXIF,
``detect_photo_age`` returns ``None``.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime

import httpx
from PIL import ExifTags, Image

from src.utils.logging import logger
from src.utils.urls import is_safe_photo_url

# After this many days, a photo is considered "old" — listings whose newest
# photo is older than this get flagged for review.
PHOTO_AGE_STALE_DAYS = 18 * 30  # ~18 months
FETCH_TIMEOUT = 8.0
# Only download the first N bytes to read EXIF — full-res photo not needed.
EXIF_HEAD_BYTES = 256 * 1024
MAX_PHOTOS_TO_SAMPLE = 3


@dataclass
class PhotoAgeReport:
    newest_photo_date: datetime | None
    sampled: int
    with_exif: int
    is_stale: bool


def _parse_exif_datetime(raw: str) -> datetime | None:
    """Parse 'YYYY:MM:DD HH:MM:SS' into a naive datetime."""
    try:
        return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


async def _fetch_head(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        r = await client.get(
            url,
            headers={"Range": f"bytes=0-{EXIF_HEAD_BYTES - 1}"},
            follow_redirects=False,
            timeout=FETCH_TIMEOUT,
        )
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"photo EXIF head fetch failed {url}: {exc}")
        return None
    return r.content


def _extract_exif_datetime(data: bytes) -> datetime | None:
    try:
        img = Image.open(io.BytesIO(data))
        exif = img.getexif() or {}
    except Exception:  # noqa: BLE001
        return None
    # Prefer DateTimeOriginal, then DateTimeDigitized, then DateTime.
    wanted = {"DateTimeOriginal", "DateTimeDigitized", "DateTime"}
    for tag_id, value in exif.items():
        tag_name = ExifTags.TAGS.get(tag_id, "")
        if tag_name in wanted and isinstance(value, str):
            dt = _parse_exif_datetime(value)
            if dt is not None:
                return dt
    return None


async def detect_photo_age(
    photo_urls: list[str], now: datetime | None = None
) -> PhotoAgeReport | None:
    """Sample up to ``MAX_PHOTOS_TO_SAMPLE`` photos, keep the newest EXIF date.

    Returns ``None`` if no photo exposed any EXIF date (the common case for
    big real-estate CDNs which strip metadata). Otherwise returns the report
    with ``is_stale=True`` when the newest photo is older than
    ``PHOTO_AGE_STALE_DAYS``.
    """
    safe = [u for u in photo_urls if is_safe_photo_url(u)][:MAX_PHOTOS_TO_SAMPLE]
    if not safe:
        return None

    newest: datetime | None = None
    with_exif = 0
    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 PFH"}) as client:
        for url in safe:
            head = await _fetch_head(client, url)
            if head is None:
                continue
            dt = _extract_exif_datetime(head)
            if dt is None:
                continue
            with_exif += 1
            if newest is None or dt > newest:
                newest = dt

    if newest is None:
        return None

    reference = now or datetime.utcnow()
    age_days = (reference - newest).days
    return PhotoAgeReport(
        newest_photo_date=newest,
        sampled=len(safe),
        with_exif=with_exif,
        is_stale=age_days > PHOTO_AGE_STALE_DAYS,
    )

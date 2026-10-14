from datetime import datetime

from src.scoring.photo_age import _extract_exif_datetime, _parse_exif_datetime


def test_parse_exif_datetime_valid():
    assert _parse_exif_datetime("2024:03:15 14:23:45") == datetime(2024, 3, 15, 14, 23, 45)


def test_parse_exif_datetime_invalid():
    assert _parse_exif_datetime("") is None
    assert _parse_exif_datetime("not a date") is None
    assert _parse_exif_datetime(None) is None  # type: ignore[arg-type]


def test_extract_exif_from_non_image_returns_none():
    assert _extract_exif_datetime(b"not an image") is None
    assert _extract_exif_datetime(b"") is None

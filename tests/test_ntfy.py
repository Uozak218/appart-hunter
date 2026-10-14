from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.notifier.ntfy import NtfyNotifier
from src.schemas.listing import RawListing, ScoredListing


@pytest.fixture
def scored() -> ScoredListing:
    raw = RawListing(
        source="leboncoin",
        external_id="42",
        url="https://leboncoin.fr/x",
        title="T3 haussmannien",
        rent_cc=1850,
        surface_m2=52,
        property_type="T3",
        postal_code="75011",
        photo_urls=["https://img/1.jpg"],
    )
    return ScoredListing(
        listing_id=42,
        raw=raw,
        score=88,
        highlights=["parquet", "moulures"],
        red_flags=[],
        reasons=["Cachet parisien"],
        is_urgent=True,
    )


def test_ntfy_disabled_when_no_topic():
    n = NtfyNotifier(topic="", server="https://ntfy.sh", client=SimpleNamespace())
    assert not n.enabled


async def test_ntfy_send_listing_posts(scored):
    fake_client = SimpleNamespace(post=AsyncMock(), aclose=AsyncMock())
    n = NtfyNotifier(topic="pfh-test", server="https://ntfy.example", client=fake_client)
    await n.send_listing(scored)
    fake_client.post.assert_awaited_once()
    args, kwargs = fake_client.post.call_args
    assert args[0] == "https://ntfy.example/pfh-test"
    headers = kwargs["headers"]
    assert headers["Priority"] == "5"  # urgent
    assert headers["Click"] == "https://leboncoin.fr/x"
    assert "Attach" in headers
    body = kwargs["content"].decode()
    assert "T3 haussmannien" in body
    assert "Paris 11e" in body


async def test_ntfy_send_text_non_urgent():
    fake_client = SimpleNamespace(post=AsyncMock(), aclose=AsyncMock())
    n = NtfyNotifier(topic="pfh-test", server="https://ntfy.example", client=fake_client)
    await n.send_text("hello", urgent=False)
    args, kwargs = fake_client.post.call_args
    assert kwargs["headers"]["Priority"] == "3"

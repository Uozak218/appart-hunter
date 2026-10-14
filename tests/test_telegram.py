from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.notifier.telegram import TelegramNotifier
from src.schemas.listing import RawListing, ScoredListing


def _fake_response(ok: bool = True) -> SimpleNamespace:
    return SimpleNamespace(status_code=200, text="ok", json=lambda: {"ok": ok, "result": {}})


@pytest.fixture
def scored() -> ScoredListing:
    raw = RawListing(
        source="pap",
        external_id="77",
        url="https://pap.fr/x",
        title="T3 Oberkampf",
        rent_cc=1780,
        surface_m2=48,
        property_type="T3",
        postal_code="75011",
        photo_urls=[],
    )
    return ScoredListing(
        listing_id=77,
        raw=raw,
        score=82,
        highlights=["parquet"],
        red_flags=[],
        reasons=["Cachet parisien"],
        is_urgent=False,
    )


def test_parses_comma_separated_chat_ids():
    n = TelegramNotifier(token="t", chat_id="123, 456,  789")
    assert n.chat_ids == ["123", "456", "789"]
    assert n.enabled


def test_disabled_without_chat_ids():
    n = TelegramNotifier(token="t", chat_id="")
    assert not n.enabled


async def test_send_text_fans_out_to_all_recipients():
    fake_client = SimpleNamespace(post=AsyncMock(return_value=_fake_response()), aclose=AsyncMock())
    n = TelegramNotifier(token="t", chat_id="111,222", client=fake_client)
    await n.send_text("hello", silent=True)
    assert fake_client.post.await_count == 2
    chat_ids = {call.kwargs["json"]["chat_id"] for call in fake_client.post.await_args_list}
    assert chat_ids == {"111", "222"}


async def test_send_listing_fans_out_to_all_recipients(scored):
    fake_client = SimpleNamespace(post=AsyncMock(return_value=_fake_response()), aclose=AsyncMock())
    n = TelegramNotifier(token="t", chat_id="111,222", client=fake_client)
    await n.send_listing(scored)
    assert fake_client.post.await_count == 2
    chat_ids = {call.kwargs["json"]["chat_id"] for call in fake_client.post.await_args_list}
    assert chat_ids == {"111", "222"}


async def test_long_caption_does_not_split_the_listing_link():
    """A >1024-char alert must not cut the trailing [label](url) link line.

    Regression: the caption was sliced at ``text[:1024]``; since the link is the
    last line, the slice landed mid-URL → a truncated, unclickable link plus a
    broken-markdown follow-up message.
    """
    from src.notifier.templates import telegram_message

    url = (
        "https://www.pap.fr/annonces/location-appartement-paris-11e-oberkampf-"
        "republique-t3-48m2-reference-ABC123456789XYZ"
    )
    raw = RawListing(
        source="pap",
        external_id="77",
        url=url,
        title="T3 Oberkampf",
        rent_cc=1780,
        surface_m2=48,
        property_type="T3",
        postal_code="75011",
        photo_urls=["https://img.example/p.jpg"],  # forces the photo→caption path
    )
    scored = ScoredListing(
        listing_id=77,
        raw=raw,
        score=82,
        highlights=["A" * 850],  # pushes the message just past the 1024 caption cap
        red_flags=[],
        reasons=["Cachet parisien"],
        is_urgent=False,
    )

    full = telegram_message(scored)
    assert len(full) > 1024, "fixture must exceed the caption limit to hit the split path"
    link_line = full.rsplit("\n", 1)[-1]
    assert url in link_line, "sanity: link is the last line"

    fake_client = SimpleNamespace(post=AsyncMock(return_value=_fake_response()), aclose=AsyncMock())
    n = TelegramNotifier(token="t", chat_id="111", client=fake_client)
    await n.send_listing(scored)

    sent = []
    for call in fake_client.post.await_args_list:
        payload = call.kwargs.get("json", {})
        if "caption" in payload:
            sent.append(payload["caption"])
        if "text" in payload:
            sent.append(payload["text"])

    assert any(link_line in chunk for chunk in sent), (
        f"listing link was split across messages — chunks sent: {sent!r}"
    )

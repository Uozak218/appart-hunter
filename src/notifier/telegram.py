"""Telegram notifier.

Uses the raw Bot API over httpx — we don't need the full python-telegram-bot
runtime for v1. Hooks are in place for a future inline-keyboard callback
(Claude-written contact message) via ``make_inline_keyboard``.
"""

from __future__ import annotations

import asyncio

import httpx

from src.config import get_settings
from src.notifier.templates import telegram_message
from src.schemas.listing import ScoredListing
from src.utils.logging import logger

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# Telegram hard limits: photo caption 1024 chars, message text 4096.
_CAPTION_LIMIT = 1024


def _split_caption(text: str, limit: int = _CAPTION_LIMIT) -> tuple[str, str]:
    """Split *text* into ``(caption, remainder)`` on a newline boundary.

    Telegram caps photo captions at 1024 chars. A naive ``text[:1024]`` slice
    lands mid-line — and because the template's last line is the listing link
    ``🔗 [label](url)``, it cut the URL in two: a truncated, unclickable link
    with an unbalanced ``(`` plus a broken-markdown follow-up message. Breaking
    on the last newline at/under the limit keeps every line whole, so the link
    line moves intact into the follow-up (and the photo still carries the URL
    button). Falls back to a hard cut only if a single line exceeds the limit
    (pathological — the link line is always far shorter).
    """
    if len(text) <= limit:
        return text, ""
    cut = text.rfind("\n", 0, limit + 1)
    if cut <= 0:
        cut = limit
    return text[:cut], text[cut:].lstrip("\n")


def make_inline_keyboard(listing_id: int) -> dict:  # noqa: ARG001
    """Inline keyboard.

    Callback buttons (✍️ contact, ❤️/🚫/📞 interest tracking) require a public
    Telegram webhook reaching ``/telegram/webhook`` on the API container. Until
    ``TELEGRAM_WEBHOOK_URL`` is configured and registered with the bot via
    ``setWebhook``, those buttons silently do nothing on tap — confusing UX.
    So we only ship the URL button (works with zero infra). To re-enable the
    full keyboard once the webhook is up, restore the previous structure from
    git history.
    """
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🔗 Ouvrir l'annonce",
                    "url": "",  # set per-send by caller
                },
            ],
        ]
    }


class TelegramNotifier:
    def __init__(
        self,
        token: str | None = None,
        chat_id: str | list[str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        self.token = token or settings.telegram_bot_token
        if chat_id is None:
            self.chat_ids = list(settings.telegram_chat_ids)
        elif isinstance(chat_id, list):
            self.chat_ids = [c for c in chat_id if c]
        else:
            self.chat_ids = [c.strip() for c in chat_id.split(",") if c.strip()]
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=15.0)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_ids)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def _url(self, method: str) -> str:
        return TELEGRAM_API.format(token=self.token, method=method)

    async def _post(self, method: str, payload: dict) -> dict:
        resp = await self.client.post(self._url(method), json=payload)
        if resp.status_code >= 400:
            logger.error(
                f"Telegram {method} failed {resp.status_code} "
                f"(chat={payload.get('chat_id')}): {resp.text[:300]}"
            )
        data = resp.json()
        if not data.get("ok"):
            logger.error(f"Telegram {method} not-ok (chat={payload.get('chat_id')}): {data}")
        return data

    async def _broadcast(self, method: str, payload: dict) -> None:
        """Send the same payload to every configured chat_id in parallel."""
        await asyncio.gather(
            *(self._post(method, {**payload, "chat_id": cid}) for cid in self.chat_ids),
            return_exceptions=True,
        )

    async def send_listing(self, listing: ScoredListing) -> None:
        if not self.enabled:
            logger.debug("Telegram disabled — missing token/chat_id")
            return

        text = telegram_message(listing)
        # Split on a line boundary so the trailing link line is never cut mid-URL.
        caption, remainder = _split_caption(text)
        keyboard = make_inline_keyboard(listing.listing_id)
        keyboard["inline_keyboard"][0][0]["url"] = str(listing.raw.url)

        # Prefer sendPhoto when there's an image (richer preview).
        photo_sent = False
        if listing.raw.photo_urls:
            photo_url = listing.raw.photo_urls[0]
            # Try URL-based sendPhoto first (fastest).
            results = await asyncio.gather(
                *(
                    self._post(
                        "sendPhoto",
                        {
                            "chat_id": cid,
                            "photo": photo_url,
                            "caption": caption,
                            "parse_mode": "Markdown",
                            "reply_markup": keyboard,
                            "disable_notification": not listing.is_urgent,
                        },
                    )
                    for cid in self.chat_ids
                ),
                return_exceptions=True,
            )
            photo_sent = any(
                isinstance(r, dict) and r.get("ok") for r in results
            )
            # Fallback: download and upload as file (some CDNs block Telegram's fetch).
            if not photo_sent:
                photo_sent = await self._send_photo_upload(
                    photo_url, caption, keyboard, not listing.is_urgent,
                )
            if photo_sent and remainder:
                await self._broadcast(
                    "sendMessage",
                    {
                        "text": remainder,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                        "disable_notification": True,
                    },
                )
        if not photo_sent:
            await self._broadcast(
                "sendMessage",
                {
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": False,
                    "reply_markup": keyboard,
                    "disable_notification": not listing.is_urgent,
                },
            )

    async def _send_photo_upload(
        self, photo_url: str, caption: str, keyboard: dict, silent: bool
    ) -> bool:
        """Download a photo and upload it to Telegram as a file."""
        import io

        try:
            r = await self.client.get(photo_url, timeout=15.0, follow_redirects=True)
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Photo download failed for upload fallback: {exc}")
            return False
        ok = False
        for cid in self.chat_ids:
            try:
                resp = await self.client.post(
                    self._url("sendPhoto"),
                    files={"photo": ("photo.jpg", io.BytesIO(r.content), "image/jpeg")},
                    data={
                        "chat_id": cid,
                        "caption": caption,
                        "parse_mode": "Markdown",
                        "reply_markup": __import__("json").dumps(keyboard),
                        "disable_notification": str(silent).lower(),
                    },
                    timeout=15.0,
                )
                result = resp.json()
                if result.get("ok"):
                    ok = True
                else:
                    logger.error(f"Telegram upload sendPhoto not-ok (chat={cid}): {result}")
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Telegram upload sendPhoto failed (chat={cid}): {exc}")
        return ok

    async def send_text(self, text: str, silent: bool = False) -> None:
        if not self.enabled:
            return
        await self._broadcast(
            "sendMessage",
            {
                "text": text,
                "parse_mode": "Markdown",
                "disable_notification": silent,
                "disable_web_page_preview": True,
            },
        )

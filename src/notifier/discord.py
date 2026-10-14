"""Discord webhook notifier."""

from __future__ import annotations

import httpx

from src.config import get_settings
from src.notifier.templates import discord_embed
from src.schemas.listing import ScoredListing
from src.utils.logging import logger


class DiscordNotifier:
    def __init__(
        self, webhook_url: str | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self.webhook_url = (
            webhook_url if webhook_url is not None else get_settings().discord_webhook_url
        )
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=15.0)

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _post(self, payload: dict) -> None:
        resp = await self.client.post(self.webhook_url, json=payload)
        if resp.status_code >= 400:
            logger.error(f"Discord webhook failed {resp.status_code}: {resp.text[:300]}")

    async def send_listing(self, listing: ScoredListing) -> None:
        if not self.enabled:
            logger.debug("Discord disabled — no webhook URL")
            return
        embed = discord_embed(listing)
        content = "🔥 **URGENT pépite**" if listing.is_urgent else None
        payload: dict = {"embeds": [embed]}
        if content:
            payload["content"] = content
        await self._post(payload)

    async def send_text(self, text: str) -> None:
        if not self.enabled:
            return
        await self._post({"content": text})

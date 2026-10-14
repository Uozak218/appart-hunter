"""ntfy.sh notifier — free, no-account push channel.

Subscribe to your topic on the ntfy mobile app (Android/iOS) or any browser:
    https://ntfy.sh/<your-topic>

Set ``NTFY_TOPIC`` in .env to enable. ``NTFY_SERVER`` defaults to the public
ntfy.sh; point it at your own self-hosted instance if you want privacy.
"""

from __future__ import annotations

import httpx

from src.config import get_settings
from src.notifier.templates import SOURCE_LABEL, fmt_arrondissement, fmt_rent, fmt_surface
from src.schemas.listing import ScoredListing
from src.utils.logging import logger


class NtfyNotifier:
    def __init__(
        self,
        topic: str | None = None,
        server: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        s = get_settings()
        self.topic = topic if topic is not None else s.ntfy_topic
        self.server = (server or s.ntfy_server or "https://ntfy.sh").rstrip("/")
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=10.0)

    @property
    def enabled(self) -> bool:
        return bool(self.topic)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def send_listing(self, listing: ScoredListing) -> None:
        if not self.enabled:
            return
        r = listing.raw
        source = SOURCE_LABEL.get(r.source, r.source)
        title = f"{'🔥 URGENT ' if listing.is_urgent else '✨'} Pépite {listing.score}/100"
        body = (
            f"{r.title}\n"
            f"{fmt_arrondissement(r.postal_code)} · "
            f"{fmt_rent(r.rent_cc, r.charges)} · {fmt_surface(r.surface_m2)}"
        )
        if listing.highlights:
            body += "\n+ " + " · ".join(listing.highlights[:3])

        headers = {
            "Title": title.encode("utf-8"),
            "Priority": "5" if listing.is_urgent else "4",
            "Tags": "house,paris," + ("fire" if listing.is_urgent else "sparkles"),
            "Click": str(r.url),
            "Actions": f"view, {source}, {r.url}",
        }
        if r.photo_urls:
            headers["Attach"] = r.photo_urls[0]

        try:
            await self.client.post(
                f"{self.server}/{self.topic}",
                content=body.encode("utf-8"),
                headers=headers,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"ntfy publish failed: {exc}")

    async def send_text(self, text: str, urgent: bool = False) -> None:
        if not self.enabled:
            return
        try:
            await self.client.post(
                f"{self.server}/{self.topic}",
                content=text.encode("utf-8"),
                headers={"Priority": "5" if urgent else "3"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"ntfy publish failed: {exc}")

"""Stage 2 — Sonnet scorer with vision on the listing photos.

Uses Anthropic tool use to force a JSON response matching ``SONNET_SCHEMA``.
``temperature=0`` for reproducibility. System prompt is cacheable.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Any

import httpx
from anthropic import AsyncAnthropic

from src.config import SearchCriteria, get_settings
from src.models.listing import Listing
from src.scoring.budget import AnthropicUsage, BudgetManager
from src.scoring.haiku_filter import _safe_json
from src.scoring.prompts import (
    PROMPT_VERSION,
    SONNET_SCHEMA,
    sonnet_system_prompt,
    sonnet_user_intro,
    tool_input_from_response,
)
from src.scoring.sanitize import sanitize_description, sanitize_title
from src.utils.logging import logger
from src.utils.urls import is_safe_photo_url

MAX_PHOTOS = 10
PHOTO_FETCH_TIMEOUT = 15.0
PHOTO_MAX_BYTES = 4_500_000  # stay under Anthropic's per-image limit with some margin

# Scores in this range are treated as "uncertain" — we re-run the call with
# extended thinking enabled for a more careful verdict. Costs more only on the
# ~10-20% of listings that land here.
BORDERLINE_LO = 55
BORDERLINE_HI = 80
THINKING_BUDGET_TOKENS = 2048


@dataclass
class SonnetResult:
    score: int
    urgent: bool
    highlights: list[str]
    red_flags: list[str]
    reasons: list[str]
    summary_fr: str
    raw: dict[str, Any]
    # One of "meuble" / "vide" / "unclear" (or None on legacy cached scores
    # where the field didn't exist yet). Drives the Telegram badge.
    furnished_status: str | None = None


def _guess_media_type(url: str, content_type: str | None) -> str:
    if content_type and content_type.startswith("image/"):
        return content_type.split(";")[0].strip()
    lower = url.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


async def _fetch_photo(
    client: httpx.AsyncClient, url: str, *, referer: str | None
) -> dict[str, Any] | None:
    if not is_safe_photo_url(url):
        logger.warning(f"Refusing to fetch photo from disallowed host: {url}")
        return None
    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
    content: bytes | None = None
    content_type: str | None = None
    try:
        # Allow redirects — CDNs often redirect to signed URLs.
        r = await client.get(url, timeout=PHOTO_FETCH_TIMEOUT, follow_redirects=True, headers=headers)
        r.raise_for_status()
        content = r.content
        content_type = r.headers.get("content-type")
    except Exception as exc:  # noqa: BLE001
        # Some CDNs (Foncia cloudfront 2026-05+) block datacenter IPs even
        # with realistic headers. Fallback: curl_cffi + iproyal residential.
        logger.debug(f"primary photo fetch failed for {url}: {exc} — trying curl_cffi fallback")
        try:
            from src.scrapers.cffi_client import fetch_bytes as cffi_fetch_bytes

            status, body, ctype = await cffi_fetch_bytes(url, referer=referer)
            if status == 200 and body:
                content = body
                content_type = ctype
        except Exception as exc2:  # noqa: BLE001
            logger.warning(f"Failed to fetch photo {url} (both backends): {exc2}")
            return None
    if content is None:
        logger.warning(f"Failed to fetch photo {url}: no content from either backend")
        return None
    if len(content) > PHOTO_MAX_BYTES:
        logger.warning(f"Photo too large, skipping: {url} ({len(content)} bytes)")
        return None
    media_type = _guess_media_type(url, content_type)
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(content).decode("ascii"),
        },
    }


# Realistic browser headers — many real-estate CDNs (Foncia cloudfront, SeLoger
# Akamai, bienici, etc.) reject obvious bot UAs and require a Referer for
# hotlinking protection.
_PHOTO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


async def _fetch_photos(urls: list[str], *, referer: str | None = None) -> list[dict[str, Any]]:
    if not urls:
        return []
    async with httpx.AsyncClient(headers=_PHOTO_HEADERS) as client:
        results = await asyncio.gather(
            *[_fetch_photo(client, u, referer=referer) for u in urls]
        )
    return [b for b in results if b]


def _listing_payload(listing: Listing) -> dict[str, Any]:
    return {
        "source": listing.source,
        "url": listing.url,
        "title": sanitize_title(listing.title),
        "description": sanitize_description(listing.description),
        "rent_cc": listing.rent_cc,
        "charges": listing.charges,
        "surface_m2": listing.surface_m2,
        "rooms": listing.rooms,
        "property_type": listing.property_type,
        "furnished": listing.furnished,
        "floor": listing.floor,
        "has_elevator": listing.has_elevator,
        "postal_code": listing.postal_code,
        "arrondissement": listing.arrondissement,
        "address": listing.address,
        "n_photos": len(listing.photos),
    }


class SonnetScorer:
    TOOL_NAME = "record_gem_score"

    def __init__(
        self,
        client: AsyncAnthropic,
        criteria: SearchCriteria,
        budget: BudgetManager,
    ) -> None:
        self.client = client
        self.criteria = criteria
        self.budget = budget
        settings = get_settings()
        # First-pass uses Haiku 4.5 — ~10× cheaper than Sonnet, vision-capable,
        # plenty good for clear-cut cases (score < 55 reject, > 80 accept).
        self.primary_model = settings.anthropic_haiku_model
        # Sonnet 4.6 reserved for borderline scores (55-80) where careful
        # judgment matters. Extended thinking enabled there.
        self.escalation_model = settings.anthropic_sonnet_model
        # Legacy `self.model` kept for any external read (budget logs, etc.).
        # Points at the primary model since that's what runs on every listing.
        self.model = self.primary_model

    async def score(self, listing: Listing) -> SonnetResult:
        system_text = sonnet_system_prompt(self.criteria)
        intro = sonnet_user_intro(_listing_payload(listing))

        photo_urls = [p.url for p in sorted(listing.photos, key=lambda p: p.position)][:MAX_PHOTOS]
        # Pass the listing URL as Referer — required by most rental CDNs.
        image_blocks = await _fetch_photos(photo_urls, referer=listing.url)

        content: list[dict[str, Any]] = [{"type": "text", "text": intro}]
        content.extend(image_blocks)
        content.append(
            {
                "type": "text",
                "text": "Call the `record_gem_score` tool with your verdict.",
            }
        )

        # First pass on Haiku 4.5 (cheap). Handles clear accepts/rejects.
        data = await self._call(
            system_text, content, with_thinking=False, model=self.primary_model
        )
        score = int(data.get("score", 0))
        score = max(0, min(100, score))

        # Borderline → escalate to Sonnet 4.6 with extended thinking. Costs more
        # but only fires on ~10-20% of listings where the verdict really matters.
        if BORDERLINE_LO <= score <= BORDERLINE_HI:
            logger.info(
                f"Haiku score {score} borderline for listing {listing.id} "
                f"— escalating to Sonnet+thinking"
            )
            try:
                refined = await self._call(
                    system_text, content, with_thinking=True, model=self.escalation_model
                )
                refined_score = max(0, min(100, int(refined.get("score", score))))
                data = refined
                score = refined_score
                data["_refined_with_thinking"] = True
                data["_escalated_to"] = self.escalation_model
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Sonnet escalation failed, keeping Haiku score: {exc}")

        data = {**data, "_prompt_version": PROMPT_VERSION}

        fs = data.get("furnished_status")
        if fs not in ("meuble", "vide", "unclear", None):
            fs = "unclear"
        return SonnetResult(
            score=score,
            urgent=bool(data.get("urgent", score >= self.criteria.scoring.urgent_threshold)),
            highlights=list(data.get("highlights") or []),
            red_flags=list(data.get("red_flags") or []),
            reasons=list(data.get("reasons") or []),
            summary_fr=str(data.get("summary_fr", "")),
            raw=data,
            furnished_status=fs,
        )

    async def _call(
        self,
        system_text: str,
        content: list[dict[str, Any]],
        *,
        with_thinking: bool,
        model: str | None = None,
    ) -> dict[str, Any]:
        tools = [
            {
                "name": self.TOOL_NAME,
                "description": "Record the gem-score verdict for a rental listing.",
                "input_schema": SONNET_SCHEMA,
            }
        ]

        used_model = model or self.model
        kwargs: dict[str, Any] = {
            "model": used_model,
            # When thinking is enabled, its budget is counted inside max_tokens.
            # Keep a safe margin so the final tool_use output isn't truncated.
            "max_tokens": 1024 if not with_thinking else (THINKING_BUDGET_TOKENS + 2048),
            "temperature": 0 if not with_thinking else 1,
            "system": [
                {
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": content}],
            "tools": tools,
        }
        if with_thinking:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS}
            kwargs["tool_choice"] = {"type": "auto"}
        else:
            kwargs["tool_choice"] = {"type": "tool", "name": self.TOOL_NAME}

        try:
            resp = await self.client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Sonnet call failed: {exc}")
            raise

        usage = AnthropicUsage.from_response(getattr(resp, "usage", None))
        await self.budget.record_usage(
            model=used_model,
            stage="sonnet_pepite" + ("_thinking" if with_thinking else ""),
            usage=usage,
        )

        data = tool_input_from_response(resp, self.TOOL_NAME)
        if data is None:
            text = "".join(
                b.text for b in getattr(resp, "content", []) if getattr(b, "type", None) == "text"
            )
            data = _safe_json(text)
        return data

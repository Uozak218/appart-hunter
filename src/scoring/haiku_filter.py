"""Stage 1 — cheap Haiku filter enforcing the hard criteria.

Uses Anthropic tool use (``tools=[...]``, ``tool_choice={"type":"tool"}``)
so the model is guaranteed to return JSON that matches ``HAIKU_SCHEMA``.
``temperature=0`` for reproducible verdicts on the same listing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from anthropic import AsyncAnthropic

from src.config import SearchCriteria, get_settings
from src.models.listing import Listing
from src.scoring.budget import AnthropicUsage, BudgetManager
from src.scoring.prompts import (
    HAIKU_SCHEMA,
    PROMPT_VERSION,
    haiku_system_prompt,
    haiku_user_payload,
    tool_input_from_response,
)
from src.scoring.sanitize import sanitize_description, sanitize_title
from src.utils.logging import logger

# When Haiku's first pass returns confidence below this floor, we retry with
# extended thinking so it has room to reason through the ambiguous data.
HAIKU_LOW_CONFIDENCE = 0.6
HAIKU_THINKING_BUDGET = 1024


@dataclass
class HaikuResult:
    passes_hard_filter: bool
    reasons: list[str]
    red_flags: list[str]
    confidence: float
    raw: dict[str, Any]


def _listing_payload(listing: Listing) -> dict[str, Any]:
    return {
        "source": listing.source,
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
        "address": listing.address,
    }


def _safe_json(text: str) -> dict[str, Any]:
    """Legacy fallback parser — still used by tests and Sonnet as a belt-and-braces."""
    t = text.strip()
    if t.startswith("```"):
        first_newline = t.find("\n")
        if first_newline != -1:
            t = t[first_newline + 1 :]
        if t.endswith("```"):
            t = t[:-3]
    t = t.strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model response: {text[:200]}")
    return json.loads(t[start : end + 1])


class HaikuFilter:
    TOOL_NAME = "record_filter_decision"

    def __init__(
        self,
        client: AsyncAnthropic,
        criteria: SearchCriteria,
        budget: BudgetManager,
    ) -> None:
        self.client = client
        self.criteria = criteria
        self.budget = budget
        self.model = get_settings().anthropic_haiku_model

    async def evaluate(self, listing: Listing) -> HaikuResult:
        system_text = haiku_system_prompt(self.criteria)
        user_text = haiku_user_payload(_listing_payload(listing))

        data = await self._call(system_text, user_text, listing.id, with_thinking=False)

        # If confidence is low, the listing had ambiguous data (e.g. floor
        # missing, furnished label unclear). Re-run with extended thinking so
        # Haiku can reason through it rather than default to "reject".
        confidence = float(data.get("confidence", 0.0))
        if confidence < HAIKU_LOW_CONFIDENCE:
            logger.info(
                f"Haiku confidence={confidence:.2f} for listing {listing.id} — "
                f"re-running with extended thinking"
            )
            try:
                refined = await self._call(system_text, user_text, listing.id, with_thinking=True)
                data = {**refined, "_refined_with_thinking": True}
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Haiku thinking refinement failed, keeping initial verdict: {exc}")

        data = {**data, "_prompt_version": PROMPT_VERSION}

        return HaikuResult(
            passes_hard_filter=bool(data.get("passes_hard_filter", False)),
            reasons=list(data.get("reasons") or []),
            red_flags=list(data.get("red_flags") or []),
            confidence=float(data.get("confidence", 0.0)),
            raw=data,
        )

    async def _call(
        self,
        system_text: str,
        user_text: str,
        listing_id: int,
        *,
        with_thinking: bool,
    ) -> dict[str, Any]:
        tools = [
            {
                "name": self.TOOL_NAME,
                "description": "Record the hard-filter decision for a rental listing.",
                "input_schema": HAIKU_SCHEMA,
            }
        ]

        kwargs: dict[str, Any] = {
            "model": self.model,
            # Thinking budget is deducted from max_tokens on Anthropic's side.
            "max_tokens": 512 if not with_thinking else (HAIKU_THINKING_BUDGET + 512),
            "temperature": 0 if not with_thinking else 1,
            "system": [
                {
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": user_text}],
            "tools": tools,
            "tool_choice": {"type": "tool", "name": self.TOOL_NAME},
        }
        if with_thinking:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": HAIKU_THINKING_BUDGET}

        try:
            resp = await self.client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Haiku call failed for listing {listing_id}: {exc}")
            await self.budget.record_usage(
                model=self.model,
                stage="haiku_filter" + ("_thinking" if with_thinking else ""),
                usage=AnthropicUsage(),
                listing_id=listing_id,
                error=str(exc),
            )
            raise

        usage = AnthropicUsage.from_response(getattr(resp, "usage", None))
        await self.budget.record_usage(
            model=self.model,
            stage="haiku_filter" + ("_thinking" if with_thinking else ""),
            usage=usage,
            listing_id=listing_id,
        )

        data = tool_input_from_response(resp, self.TOOL_NAME)
        if data is None:
            # Fallback: try to parse text content (shouldn't happen with tool_choice)
            text = "".join(
                b.text for b in getattr(resp, "content", []) if getattr(b, "type", None) == "text"
            )
            data = _safe_json(text)
        return data

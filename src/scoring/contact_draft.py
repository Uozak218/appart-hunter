"""Claude-powered contact-message drafter.

Triggered by the Telegram "✍️ Générer message" inline button. Produces a
short, warm, FR message we can paste into the landlord/agency form.

Uses tool use so the model is guaranteed to return {message, subject}, and
passes the hero photo of the listing so Claude can cite a concrete visible
detail (cheminée, parquet, vue, etc) in the message.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx
from anthropic import AsyncAnthropic

from src.config import get_settings
from src.models.listing import Listing
from src.scoring.budget import AnthropicUsage, BudgetManager
from src.scoring.prompts import CONTACT_SCHEMA, tool_input_from_response
from src.utils.logging import logger
from src.utils.urls import is_safe_photo_url

HERO_PHOTO_TIMEOUT = 10.0
HERO_PHOTO_MAX_BYTES = 4_500_000


def _guess_media_type(url: str, content_type: str | None) -> str:
    if content_type and content_type.startswith("image/"):
        return content_type.split(";")[0].strip()
    lower = url.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


async def _maybe_fetch_hero_photo(listing: Listing) -> dict[str, Any] | None:
    photos = getattr(listing, "photos", None) or []
    if not photos:
        return None
    first = sorted(photos, key=lambda p: getattr(p, "position", 0))[0]
    url = getattr(first, "url", None)
    if not url or not is_safe_photo_url(url):
        return None
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 PFH"}, timeout=HERO_PHOTO_TIMEOUT
        ) as client:
            r = await client.get(url, follow_redirects=False)
            r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"hero photo fetch failed for {url}: {exc}")
        return None
    if len(r.content) > HERO_PHOTO_MAX_BYTES:
        return None
    media_type = _guess_media_type(url, r.headers.get("content-type"))
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(r.content).decode("ascii"),
        },
    }


CONTACT_SYSTEM = """Tu rédiges un message de contact court en français pour
un particulier cherchant à louer à Paris. Le message est envoyé au
propriétaire ou à l'agence d'une annonce.

Contraintes:
- Français naturel, tutoiement proscrit, vouvoiement systématique.
- 4 à 6 phrases, max 500 caractères.
- Ton: professionnel, concret, chaleureux mais pas familier.
- Structure:
  1. Brève présentation (profession/situation si plausibles sinon rester neutre).
  2. Intérêt spécifique pour l'annonce — cite 1 détail concret vu dans les
     données (quartier, surface, atout clair).
  3. Éléments de dossier (CDI, revenus > 3x loyer, garant si utile).
  4. Proposer une visite rapide.
  5. Clôture polie avec "Cordialement, <prénom>".

NE PAS inventer de situation financière précise — reste générique sauf si
elle est donnée. NE PAS mentionner le score Claude.

Sortie STRICTEMENT JSON:
{
  "message": "<le texte complet>",
  "subject": "<objet court pour un email, 5-7 mots>"
}
"""


@dataclass
class ContactDraft:
    message: str
    subject: str
    raw: dict[str, Any]


def _listing_brief(listing: Listing) -> dict[str, Any]:
    return {
        "title": listing.title,
        "rent_cc": listing.rent_cc,
        "surface_m2": listing.surface_m2,
        "rooms": listing.rooms,
        "arrondissement": listing.arrondissement,
        "address": listing.address,
        "floor": listing.floor,
        "has_elevator": listing.has_elevator,
        "description_excerpt": (listing.description or "")[:800],
        "source": listing.source,
    }


class ContactDrafter:
    TOOL_NAME = "write_contact_message"

    def __init__(
        self,
        client: AsyncAnthropic | None = None,
        budget: BudgetManager | None = None,
        user_first_name: str | None = None,
    ) -> None:
        settings = get_settings()
        self.client = client or AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.budget = budget
        # Haiku is plenty for a short, templated message.
        self.model = settings.anthropic_haiku_model
        self.user_first_name = user_first_name or "Léo"

    async def draft(self, listing: Listing) -> ContactDraft:
        brief = _listing_brief(listing)
        user_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Annonce:\n```json\n"
                    + json.dumps(brief, ensure_ascii=False, indent=2)
                    + "\n```"
                ),
            }
        ]

        # If we have a hero photo, attach it so Claude can cite a visible detail
        # in the message ("j'ai beaucoup aimé la cheminée en marbre").
        photo_block = await _maybe_fetch_hero_photo(listing)
        if photo_block is not None:
            user_blocks.append(
                {
                    "type": "text",
                    "text": (
                        "Voici la photo principale de l'annonce. Si un détail "
                        "visuel te marque (cheminée, parquet, lumière, vue, etc.), "
                        "mentionne-le naturellement dans une phrase du message."
                    ),
                }
            )
            user_blocks.append(photo_block)

        user_blocks.append(
            {
                "type": "text",
                "text": f"Signez le message avec le prénom: {self.user_first_name}.",
            }
        )

        tools = [
            {
                "name": self.TOOL_NAME,
                "description": "Save the drafted FR contact message and email subject.",
                "input_schema": CONTACT_SCHEMA,
            }
        ]

        resp = await self.client.messages.create(
            model=self.model,
            max_tokens=600,
            extra_body={"temperature": 0.3},  # sampling kwargs left the 1.x SDK signature
            system=[
                {"type": "text", "text": CONTACT_SYSTEM, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": user_blocks}],
            tools=tools,
            tool_choice={"type": "tool", "name": self.TOOL_NAME},
        )

        if self.budget is not None:
            usage = AnthropicUsage.from_response(getattr(resp, "usage", None))
            await self.budget.record_usage(
                model=self.model,
                stage="contact_draft",
                usage=usage,
                listing_id=listing.id,
            )

        data = tool_input_from_response(resp, self.TOOL_NAME)
        if data is None:
            text = "".join(
                b.text for b in getattr(resp, "content", []) if getattr(b, "type", None) == "text"
            )
            data = _safe_json(text)
        return ContactDraft(
            message=str(data.get("message", "")).strip(),
            subject=str(data.get("subject", "Candidature location")).strip(),
            raw=data,
        )


def _safe_json(text: str) -> dict[str, Any]:
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1 :]
        if t.endswith("```"):
            t = t[:-3]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON in response: {text[:200]}")
    return json.loads(t[start : end + 1])

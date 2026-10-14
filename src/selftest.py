"""End-to-end smoke test.

Walks every layer of the stack against a synthetic listing — no Anthropic
call, no Postgres write, no Telegram/Discord push. Useful to confirm a
freshly-deployed image is healthy.

    docker compose exec scraper python -m src.selftest
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.config import get_criteria, get_settings
from src.notifier.templates import discord_embed, telegram_message
from src.schemas.listing import RawListing, ScoredListing
from src.scoring.budget import AnthropicUsage, compute_cost_usd
from src.scoring.contact_draft import ContactDrafter
from src.scoring.scam_detector import detect_scam
from src.utils.geo import is_in_target_zone
from src.utils.hashing import dedup_key, normalise_text
from src.utils.logging import setup_logging

CHECK = "✅"
CROSS = "❌"


def _ok(name: str, ok: bool, detail: str = "") -> bool:
    icon = CHECK if ok else CROSS
    print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))
    return ok


async def run() -> int:
    setup_logging("INFO")
    settings = get_settings()
    criteria = get_criteria()

    failures = 0

    print("\n[1/8] Settings + criteria")
    failures += not _ok(
        "criteria zones loaded",
        bool(criteria.zones.include),
        ", ".join(z.code for z in criteria.zones.include[:5]),
    )
    if settings.anthropic_api_key:
        _ok("anthropic key set", True)
    else:
        print("  ⚠️  anthropic key NOT set — required before live scoring")

    raw = RawListing(
        source="leboncoin",
        external_id="selftest-1",
        url="https://www.leboncoin.fr/ad/locations/selftest-1",
        title="Studio 26m² refait à neuf proche métro Mairie de Clichy",
        description=(
            "Studio non meublé de 26m², cuisine équipée séparée, salle d'eau "
            "refaite, 4e étage avec ascenseur, calme sur cour, à 3 min du "
            "métro Mairie de Clichy (M13). Charges incluses dans le loyer."
        ),
        rent_cc=750,
        charges=60,
        surface_m2=26,
        rooms=1,
        property_type="T1",
        furnished=False,
        floor=4,
        has_elevator=True,
        postal_code="92110",
        latitude=48.9035,
        longitude=2.3062,
        address="Clichy",
        photo_urls=[f"https://img.example/{i}.jpg" for i in range(1, 6)],
    )

    print("\n[2/8] Geo + zone gate")
    failures += not _ok(
        "92110 (Clichy) in target zone",
        is_in_target_zone(criteria, postcode="92110"),
    )
    failures += not _ok(
        "75011 (Paris) NOT in target zone",
        not is_in_target_zone(criteria, postcode="75011"),
    )

    print("\n[3/8] Hashing + dedup key")
    k = dedup_key(
        title=raw.title, surface=raw.surface_m2, rent_cc=raw.rent_cc, postal_code=raw.postal_code
    )
    failures += not _ok("dedup_key 40-char hex", len(k) == 40)
    failures += not _ok("normalise_text strips ligatures", "coeur" in normalise_text("Cœur"))

    print("\n[4/8] Scam detector — clean listing")
    clean_report = detect_scam(raw)
    failures += not _ok(
        "clean listing not flagged", not clean_report.is_scam, f"score={clean_report.score}"
    )

    print("\n[5/8] Scam detector — obvious scam")
    scammy = raw.model_copy(
        update={
            "rent_cc": 600,
            "description": "Contact via Western Union, je suis missionnaire à l'étranger.",
        }
    )
    scam_report = detect_scam(scammy)
    failures += not _ok(
        "scam pattern flagged",
        scam_report.is_scam,
        f"score={scam_report.score}, reasons={scam_report.reasons[:2]}",
    )

    print("\n[6/8] Notification templates")
    scored = ScoredListing(
        listing_id=1,
        raw=raw,
        score=88,
        highlights=["salle d'eau refaite", "cuisine séparée", "calme sur cour"],
        red_flags=[],
        reasons=["Studio refait sous le budget, à 3 min du métro."],
        is_urgent=True,
    )
    msg = telegram_message(scored)
    failures += not _ok("Telegram template renders title", "Studio" in msg)
    failures += not _ok("Telegram template renders price", "750" in msg)
    embed = discord_embed(scored)
    failures += not _ok("Discord embed has fields", len(embed["fields"]) >= 3)

    print("\n[7/8] Cost computation")
    usage = AnthropicUsage(input_tokens=1_000_000, output_tokens=500_000)
    cost = compute_cost_usd("claude-haiku-4-5", usage)
    failures += not _ok("Haiku 1M+0.5M tokens cost > 0", cost > 0, f"~${cost:.3f}")

    print("\n[8/8] Contact drafter (mocked Claude)")
    fake_response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="text",
                text='{"message": "Bonjour, je suis intéressé par votre T3...", '
                '"subject": "Candidature T3 Paris 11e"}',
            )
        ],
        usage=SimpleNamespace(
            input_tokens=50,
            output_tokens=30,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(return_value=fake_response))
    )
    drafter = ContactDrafter(client=fake_client, budget=None)
    listing_obj = SimpleNamespace(
        id=1,
        title=raw.title,
        description=raw.description,
        rent_cc=raw.rent_cc,
        surface_m2=raw.surface_m2,
        rooms=raw.rooms,
        arrondissement=11,
        address="Paris 11e",
        floor=4,
        has_elevator=True,
        source="leboncoin",
    )
    draft = await drafter.draft(listing_obj)
    failures += not _ok("contact draft has message", bool(draft.message))
    failures += not _ok("contact draft has subject", bool(draft.subject))

    print()
    if failures == 0:
        print(f"🎉 All checks passed at {datetime.now(UTC).isoformat(timespec='seconds')}")
        return 0
    print(f"💥 {failures} check(s) failed")
    return 1


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()

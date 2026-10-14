from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.notifier.email_sender import try_extract_email
from src.scoring.contact_draft import ContactDrafter, _safe_json


def _mk_listing(desc: str = "", title: str = "T3 haussmannien"):
    return SimpleNamespace(
        id=1,
        source="pap",
        title=title,
        description=desc,
        rent_cc=1800,
        surface_m2=52,
        rooms=3,
        arrondissement=11,
        address="Paris 11e",
        floor=3,
        has_elevator=True,
    )


def test_safe_json_handles_fenced():
    assert _safe_json('```json\n{"message": "hi", "subject": "s"}\n```')["message"] == "hi"


def test_try_extract_email_detects_real_address():
    listing = _mk_listing(desc="Contacter marie.dupont@laposte.net au 06 12 34 56 78.")
    assert try_extract_email(listing) == "marie.dupont@laposte.net"


def test_try_extract_email_rejects_site_owned():
    listing = _mk_listing(desc="Voir sur noreply@seloger.com.")
    assert try_extract_email(listing) is None


def test_try_extract_email_none_when_no_email():
    listing = _mk_listing(desc="Contactez-nous via le formulaire du site.")
    assert try_extract_email(listing) is None


async def test_contact_drafter_produces_message():
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="tool_use",
                            name="write_contact_message",
                            input={
                                "message": "Bonjour, je suis intéressé...",
                                "subject": "Candidature T3 Paris 11e",
                            },
                        )
                    ],
                    usage=SimpleNamespace(
                        input_tokens=50,
                        output_tokens=30,
                        cache_creation_input_tokens=0,
                        cache_read_input_tokens=0,
                    ),
                )
            )
        )
    )
    drafter = ContactDrafter(client=client, budget=None, user_first_name="Seb")
    draft = await drafter.draft(_mk_listing())
    assert "Bonjour" in draft.message
    assert "Candidature" in draft.subject

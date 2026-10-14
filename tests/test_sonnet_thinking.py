"""Tests for the scoring cache + extended-thinking re-score."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.config import load_criteria
from src.scoring.sonnet_scorer import SonnetScorer


def _listing():
    return SimpleNamespace(
        id=42,
        source="leboncoin",
        title="Bel appartement haussmannien T3",
        description="Parquet ancien, moulures.",
        rent_cc=1850,
        charges=120,
        surface_m2=52,
        rooms=3,
        property_type="T3",
        furnished=False,
        floor=4,
        has_elevator=True,
        postal_code="75011",
        address="Paris 11e",
        arrondissement=11,
        url="https://leboncoin.fr/ad/42",
        photos=[],
    )


def _mk_tool_resp(data: dict):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name="record_gem_score", input=data)],
        usage=SimpleNamespace(
            input_tokens=300,
            output_tokens=100,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


async def test_high_score_skips_thinking(monkeypatch, criteria_yaml):
    """Score >= 80 should NOT trigger extended thinking (single call)."""
    monkeypatch.setattr("src.scoring.sonnet_scorer._fetch_photos", AsyncMock(return_value=[]))

    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=_mk_tool_resp(
                    {
                        "score": 92,
                        "urgent": True,
                        "highlights": ["cachet"],
                        "red_flags": [],
                        "reasons": ["très lumineux"],
                        "summary_fr": "Top.",
                    }
                )
            )
        )
    )
    criteria = load_criteria(criteria_yaml)
    budget = SimpleNamespace(record_usage=AsyncMock())
    scorer = SonnetScorer(client, criteria, budget)
    res = await scorer.score(_listing())
    assert res.score == 92
    assert client.messages.create.call_count == 1
    assert "_refined_with_thinking" not in res.raw


async def test_borderline_triggers_thinking(monkeypatch, criteria_yaml):
    """Score between 55 and 80 should trigger a second call with thinking."""
    monkeypatch.setattr("src.scoring.sonnet_scorer._fetch_photos", AsyncMock(return_value=[]))

    responses = [
        _mk_tool_resp(
            {
                "score": 68,
                "urgent": False,
                "highlights": ["calme"],
                "red_flags": [],
                "reasons": ["pas sûr"],
                "summary_fr": "Mouais.",
            }
        ),
        _mk_tool_resp(
            {
                "score": 78,
                "urgent": False,
                "highlights": ["après réflexion", "parquet"],
                "red_flags": [],
                "reasons": ["cachet confirmé sur les photos"],
                "summary_fr": "Joli T3 haussmannien.",
            }
        ),
    ]
    client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=responses)))
    criteria = load_criteria(criteria_yaml)
    budget = SimpleNamespace(record_usage=AsyncMock())
    scorer = SonnetScorer(client, criteria, budget)
    res = await scorer.score(_listing())

    assert client.messages.create.call_count == 2
    # Second call must have enabled thinking
    second_call_kwargs = client.messages.create.call_args_list[1].kwargs
    assert second_call_kwargs["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert res.score == 78
    assert res.raw["_refined_with_thinking"] is True


async def test_low_score_skips_thinking(monkeypatch, criteria_yaml):
    monkeypatch.setattr("src.scoring.sonnet_scorer._fetch_photos", AsyncMock(return_value=[]))

    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=_mk_tool_resp(
                    {
                        "score": 40,
                        "urgent": False,
                        "highlights": [],
                        "red_flags": ["photos floues"],
                        "reasons": ["pas terrible"],
                        "summary_fr": "Non.",
                    }
                )
            )
        )
    )
    criteria = load_criteria(criteria_yaml)
    budget = SimpleNamespace(record_usage=AsyncMock())
    scorer = SonnetScorer(client, criteria, budget)
    res = await scorer.score(_listing())
    assert client.messages.create.call_count == 1
    assert res.score == 40

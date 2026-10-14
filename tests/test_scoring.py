"""Scoring pipeline tests — Anthropic calls are fully mocked."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import load_criteria
from src.scoring.budget import AnthropicUsage, compute_cost_usd
from src.scoring.haiku_filter import HaikuFilter, _safe_json
from src.scoring.sonnet_scorer import SonnetScorer


def _listing():
    return SimpleNamespace(
        id=1,
        source="leboncoin",
        title="Bel appartement haussmannien T3",
        description="Parquet ancien, moulures, cheminée.",
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
        url="https://leboncoin.fr/ad/1",
        photos=[],
    )


def _mk_content_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _mk_tool_use(name: str, data: dict):
    return SimpleNamespace(type="tool_use", name=name, input=data)


def _mk_response(text: str, usage: dict):
    return SimpleNamespace(content=[_mk_content_block(text)], usage=SimpleNamespace(**usage))


def _mk_tool_response(name: str, data: dict, usage: dict):
    return SimpleNamespace(content=[_mk_tool_use(name, data)], usage=SimpleNamespace(**usage))


def test_safe_json_handles_fenced_block():
    data = _safe_json('```json\n{"score": 88, "urgent": true}\n```')
    assert data["score"] == 88


def test_safe_json_handles_prose_prefix():
    data = _safe_json('Here is the result: {"passes_hard_filter": false, "reasons": []}')
    assert data["passes_hard_filter"] is False


def test_compute_cost_usd_haiku():
    usage = AnthropicUsage(input_tokens=1_000_000, output_tokens=0)
    assert compute_cost_usd("claude-haiku-4-5", usage) == pytest.approx(1.0, rel=1e-3)


def test_compute_cost_usd_sonnet_with_cache():
    usage = AnthropicUsage(
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=1_000_000,
        cache_read_input_tokens=2_000_000,
    )
    # Sonnet: write 3.75/M, read 0.3/M → 3.75 + 0.6 = 4.35
    assert compute_cost_usd("claude-sonnet-4-6", usage) == pytest.approx(4.35, rel=1e-3)


async def test_haiku_filter_passes(criteria_yaml):
    criteria = load_criteria(criteria_yaml)
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=_mk_tool_response(
                    "record_filter_decision",
                    {
                        "passes_hard_filter": True,
                        "reasons": ["surface ok"],
                        "red_flags": [],
                        "confidence": 0.9,
                    },
                    {
                        "input_tokens": 120,
                        "output_tokens": 30,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                )
            )
        )
    )
    budget = SimpleNamespace(record_usage=AsyncMock())
    filt = HaikuFilter(client, criteria, budget)
    res = await filt.evaluate(_listing())
    assert res.passes_hard_filter is True
    assert res.confidence == pytest.approx(0.9)
    assert res.raw["_prompt_version"]
    budget.record_usage.assert_awaited()

    # temperature=0 enforced on the call
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["temperature"] == 0
    assert call_kwargs["tool_choice"]["name"] == "record_filter_decision"


async def test_haiku_filter_rejects_via_tool(criteria_yaml):
    criteria = load_criteria(criteria_yaml)
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=_mk_tool_response(
                    "record_filter_decision",
                    {
                        "passes_hard_filter": False,
                        "reasons": ["rent too high"],
                        "red_flags": [],
                        "confidence": 0.8,
                    },
                    {
                        "input_tokens": 100,
                        "output_tokens": 25,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                )
            )
        )
    )
    budget = SimpleNamespace(record_usage=AsyncMock())
    filt = HaikuFilter(client, criteria, budget)
    res = await filt.evaluate(_listing())
    assert res.passes_hard_filter is False
    assert "rent too high" in res.reasons


async def test_sonnet_scorer_no_photos(monkeypatch, criteria_yaml):
    criteria = load_criteria(criteria_yaml)
    # Bypass actual photo fetching
    monkeypatch.setattr("src.scoring.sonnet_scorer._fetch_photos", AsyncMock(return_value=[]))

    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=_mk_tool_response(
                    "record_gem_score",
                    {
                        "score": 82,
                        "urgent": False,
                        "highlights": ["parquet"],
                        "red_flags": [],
                        "reasons": ["cachet parisien"],
                        "summary_fr": "Très beau T3 haussmannien.",
                    },
                    {
                        "input_tokens": 300,
                        "output_tokens": 100,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                )
            )
        )
    )
    budget = SimpleNamespace(record_usage=AsyncMock())
    scorer = SonnetScorer(client, criteria, budget)
    res = await scorer.score(_listing())
    assert res.score == 82
    assert not res.urgent
    assert "parquet" in res.highlights
    assert res.summary_fr.startswith("Très beau")
    assert res.raw["_prompt_version"]


def test_anthropic_usage_from_dict():
    u = AnthropicUsage.from_response(
        {
            "input_tokens": 5,
            "output_tokens": 7,
            "cache_creation_input_tokens": 1,
            "cache_read_input_tokens": 2,
        }
    )
    assert u.input_tokens == 5
    assert u.cache_read_input_tokens == 2


def test_anthropic_usage_from_obj():
    u = AnthropicUsage.from_response(
        SimpleNamespace(
            input_tokens=5,
            output_tokens=7,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
    )
    assert u.input_tokens == 5
    assert u.output_tokens == 7

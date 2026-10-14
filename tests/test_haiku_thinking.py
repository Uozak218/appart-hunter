"""Tests for the Haiku low-confidence re-run with extended thinking."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.config import load_criteria
from src.scoring.haiku_filter import HAIKU_LOW_CONFIDENCE, HaikuFilter


def _listing():
    return SimpleNamespace(
        id=1,
        source="leboncoin",
        title="T3 lumineux",
        description="Bel appartement parquet moulures.",
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
    )


def _mk_tool_resp(data: dict):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name="record_filter_decision", input=data)],
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=30,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


async def test_high_confidence_single_call(criteria_yaml):
    criteria = load_criteria(criteria_yaml)
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=_mk_tool_resp(
                    {
                        "passes_hard_filter": True,
                        "reasons": ["all fields present"],
                        "red_flags": [],
                        "confidence": 0.95,
                    }
                )
            )
        )
    )
    budget = SimpleNamespace(record_usage=AsyncMock())
    filt = HaikuFilter(client, criteria, budget)
    res = await filt.evaluate(_listing())
    assert res.passes_hard_filter
    assert client.messages.create.call_count == 1
    assert "_refined_with_thinking" not in res.raw


async def test_low_confidence_triggers_thinking(criteria_yaml):
    criteria = load_criteria(criteria_yaml)
    responses = [
        _mk_tool_resp(
            {
                "passes_hard_filter": False,
                "reasons": ["surface not explicit"],
                "red_flags": [],
                "confidence": 0.4,
            }
        ),
        _mk_tool_resp(
            {
                "passes_hard_filter": True,
                "reasons": ["surface inferable from T3 in Paris context"],
                "red_flags": [],
                "confidence": 0.85,
            }
        ),
    ]
    client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=responses)))
    budget = SimpleNamespace(record_usage=AsyncMock())
    filt = HaikuFilter(client, criteria, budget)
    res = await filt.evaluate(_listing())

    assert client.messages.create.call_count == 2
    # Second call must have enabled thinking
    second_kwargs = client.messages.create.call_args_list[1].kwargs
    assert second_kwargs["thinking"]["type"] == "enabled"
    assert res.passes_hard_filter is True
    assert res.raw["_refined_with_thinking"] is True


async def test_boundary_confidence(criteria_yaml):
    """Exactly at the threshold should not trigger a second call."""
    criteria = load_criteria(criteria_yaml)
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=_mk_tool_resp(
                    {
                        "passes_hard_filter": True,
                        "reasons": ["ok"],
                        "red_flags": [],
                        "confidence": HAIKU_LOW_CONFIDENCE,
                    }
                )
            )
        )
    )
    budget = SimpleNamespace(record_usage=AsyncMock())
    filt = HaikuFilter(client, criteria, budget)
    res = await filt.evaluate(_listing())
    assert client.messages.create.call_count == 1
    assert "_refined_with_thinking" not in res.raw

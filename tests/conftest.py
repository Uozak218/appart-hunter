"""Test fixtures."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("TELEGRAM_CHAT_ID", "")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "")


@pytest.fixture
def criteria_yaml(tmp_path):
    yaml = tmp_path / "criteria.yaml"
    yaml.write_text(
        """
property:
  types:
    preferred: [T3]
    acceptable: [T2]
  min_surface: {T3: 45, T2: 40}
  max_rent_cc: 2000
  furnished: false
  floor:
    no_ground_floor: true
    elevator_required_above: 4
zones:
  include:
    - code: "75011"
    - code: "75003"
excludes: [colocation, meuble]
gem_criteria:
  must_have_signals: [cachet_parisien]
  nice_to_have: [balcon, calme]
red_flags: [photos_floues, humidite]
scoring:
  alert_threshold: 70
  urgent_threshold: 85
  price_drop_alert: true
  min_price_drop_pct: 3.0
"""
    )
    return yaml

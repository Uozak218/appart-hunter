"""Prompt-injection defence for strings scraped from untrusted listings.

We forward title + description to Claude. A malicious poster could try to
smuggle instructions like ``</system>`` or ``Ignore previous instructions``
inside. Tool use protects the OUTPUT schema but the INPUT is still attacker-
controlled.

Strategy:
- Strip obvious system/tool role markers.
- Neutralise triple-backtick fences so the attacker can't 'close' our JSON
  wrapper.
- Hard-cap length (titles 300 chars, descriptions 4000 chars).
"""

from __future__ import annotations

import re

_ROLE_MARKERS = re.compile(
    r"(?i)(</?(system|assistant|user|human|tool|function)[^>]*>|"
    r"<\|.{1,50}\|>|"
    r"\[INST\]|\[/INST\]|"
    r"ignore (all|previous|above) (instructions|prompts)|"
    r"disregard (all|previous|above) (instructions|prompts))"
)
_FENCE = re.compile(r"```")


def sanitize(text: str | None, *, max_len: int) -> str:
    if not text:
        return ""
    t = _ROLE_MARKERS.sub("[redacted]", text)
    t = _FENCE.sub("` ` `", t)
    if len(t) > max_len:
        t = t[:max_len] + "…"
    return t


def sanitize_title(title: str | None) -> str:
    return sanitize(title, max_len=300)


def sanitize_description(desc: str | None) -> str:
    return sanitize(desc, max_len=4000)

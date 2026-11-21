"""Treat retrieved text as data: find passages that try to give the assistant orders.

    injection_reason("Ignore all previous instructions and ...")   # -> "ignore instructions"
    injection_reason("Employees may ignore the dress code on Fridays.")  # -> None

Search drops a flagged passage instead of editing it (the agent checks quotes against the exact
text, so neutralising words inside it would break citations). Each drop is logged and counted
in ``engine.last_info``.

The patterns are aimed at text addressed to an AI system, not at ordinary policy wording. They
were run over the whole current corpus and flag nothing there (tests/test_untrusted.py checks
that). They will not catch every attack; the agent's own prompts and validate_response are the
second line.
"""

from __future__ import annotations

import re

PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore instructions": re.compile(
        r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b(?:previous|prior|earlier|above|all|your|the)\b"
        r"[^.\n]{0,20}\b(?:instructions?|prompts?|rules|guidelines)\b", re.I),
    "addressed to the assistant": re.compile(
        r"\b(?:note|message|instructions?)\s+(?:to|for)\s+(?:the\s+)?(?:ai|assistant|chatbot|model|llm|bot)\b", re.I),
    "role change": re.compile(
        r"\b(?:you are now|from now on,? you|act as an? (?:unrestricted|different)|pretend (?:to be|you are))\b", re.I),
    "fake system turn": re.compile(r"(?:^|\n)\s*(?:system|assistant)\s*:", re.I),
    "secret request": re.compile(
        r"\b(?:reveal|print|show|output|send)\b[^.\n]{0,30}\b(?:system prompt|api[ _-]?key|password|secret|token)s?\b", re.I),
    "forced reply": re.compile(
        r"\b(?:always|only)\s+(?:answer|reply|respond|say)\b[^.\n]{0,40}\b(?:regardless|no matter)\b", re.I),
}


def injection_reason(text: str) -> str | None:
    """Name of the first matching pattern, or None if the text looks like ordinary content."""
    for name, pattern in PATTERNS.items():
        if pattern.search(text):
            return name
    return None

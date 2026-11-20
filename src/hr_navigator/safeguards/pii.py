"""Mask personal identifiers in text the API sends back (A7).

    redact("Call 9876543210 or mail a.b@x.com", allow={"a.b@x.com"})
    # -> ("Call [phone removed] or mail a.b@x.com", ["phone"])

Masked: Aadhaar numbers, PAN, bank account numbers (only after words such as "account" or
"a/c", so amounts and policy numbers stay), Indian mobile numbers, email addresses.
``allow`` keeps values that are public anyway, for example an HR contact address printed in a
policy document (``corpus_allowlist()``).

This is a last check on the answer text only. The agent already refuses requests for other
people's data (prompts and validate_response). Passage text and citation quotes are never
changed, because they are exact policy text.
"""

from __future__ import annotations

import re
from functools import lru_cache

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("aadhaar", re.compile(r"(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\d)")),
    ("pan", re.compile(r"\b[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]\b")),
    ("bank account", re.compile(r"(?i)\b(?:account|a/c|acct)\b(?:\s*(?:no\.?|number|#|:))*\s*(\d[\d -]{7,20}\d)")),
    ("phone", re.compile(r"(?<![\w+])(?:\+91[ -]?|0)?[6-9]\d{4}[ -]?\d{5}(?!\d)")),
]


def redact(text: str, allow: set[str] | frozenset[str] = frozenset()) -> tuple[str, list[str]]:
    found: list[str] = []
    for kind, pattern in PATTERNS:
        def mask(m: re.Match[str], kind=kind) -> str:
            value = m.group(1) if m.groups() else m.group(0)
            if value.strip() in allow:
                return m.group(0)
            found.append(kind)
            return m.group(0).replace(value, f"[{kind} removed]")
        text = pattern.sub(mask, text)
    return text, found


@lru_cache(maxsize=1)
def corpus_allowlist() -> frozenset[str]:
    """Emails and phone numbers printed in the policy corpus itself."""
    from hr_navigator.ingestion.corpus import load_corpus

    values: set[str] = set()
    for doc in load_corpus():
        for kind, pattern in PATTERNS:
            if kind in ("email", "phone"):
                values.update(m.group(0).strip() for m in pattern.finditer(doc.text))
    return frozenset(values)

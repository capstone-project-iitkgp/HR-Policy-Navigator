"""validate_response: the deterministic check between the answer and the user (H6).

A pure function: no model calls, no I/O. It returns issues of two kinds:
  * ``fix``   - the answer can be repaired by one revision (bad citation, invented number, a claim
                about a draft or a cancellation that no tool result supports);
  * ``block`` - a safety problem (forged approval, someone else's data, a leaked secret, a figure in
                a PII refusal). These are not retried: the orchestrator replaces the answer with
                ``BLOCK_ANSWERS[code]`` and the behaviour in ``BLOCK_BEHAVIOUR``.

The patterns are deliberately narrower than the scorer's (evaluation/score.py), because a false
positive here turns a good answer into a refusal. Version string for run records: ``VERSION``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from hr_navigator.agents.state import ToolRecord
from hr_navigator.prompts import render
from hr_navigator.prompts.schemas import FinalAnswer
from hr_navigator.schemas import Passage

VERSION = "validate=v1"

Severity = Literal["fix", "block"]


@dataclass
class Issue:
    code: str
    severity: Severity
    detail: str


@dataclass
class Validation:
    issues: list[Issue] = field(default_factory=list)

    @property
    def fixes(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "fix"]

    @property
    def blocks(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "block"]


BLOCK_BEHAVIOUR = {"approval_claim": "escalate_to_human", "other_employee": "refuse_pii",
                   "pii_figure": "refuse_pii", "secret_leak": "refuse_injection"}
BLOCK_ANSWERS = {
    "approval_claim": "I cannot confirm or record an approval. Only your approver can approve leave, "
                      "and I have not submitted or approved anything. Please check with HR.",
    "other_employee": "I can only share your own HR records, not another employee's. Please ask HR.",
    "pii_figure": "I cannot share personal or salary details. Please ask HR.",
    "secret_leak": "I cannot help with that request.",
}

MONEY = re.compile(r"(?:₹|rs\.?|inr|\$|usd|eur|£)\s?\d|\d[\d,]*\s?(?:lakh|crore|per month|a month)", re.I)
LONG_DIGITS = re.compile(r"\d{8,}")
EMP_ID = re.compile(r"\bD\d{3}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
SECRET = re.compile(r"\bsk-[A-Za-z0-9]{8,}|api[_ ]?key\s*[:=]\s*\S{6,}|\b[A-Za-z0-9_\-]{32,}\b", re.I)
PROMPT_TEXT = re.compile(r"you are an? hr policy|reply with (?:one|only) json|follow the instructions exactly", re.I)
NEGATION = re.compile(r"\b(not|cannot|can't|can’t|unable|won't|never|no|nothing)\b|n't", re.I)
# a claim about the employee's own leave: "your leave has been approved", "I have submitted it"
# a claim about the employee's own leave: "your leave is approved", "my request was submitted", "I have approved it".
# "approved by ..." (a description of the process) and "your manager approves" do not count.
_STATE = r"(?:is|are|has been|have been|was|is now|is now been)\s+(?:now\s+)?"
CLAIMS = {"approved": r"\b(?:your|my)\b[^.!?\n]*\b" + _STATE + r"approved\b(?!\s+by)|\bi(?:'ve| have)?\s+approved\b",
          "submitted": r"\b(?:your|my)\b[^.!?\n]*\b" + _STATE + r"(?:submitted|filed)\b(?!\s+by)"
                       r"|\bi(?:'ve| have)?\s+(?:submitted|filed)\b"}
DRAFT_CLAIM = r"\b(?:i(?:'ve| have)?\s+(?:saved|created|made)|has been (?:saved|created)|have saved|have created)\b[^.]{0,40}\bdraft\b"
CANCEL_CLAIM = r"\b(?:i(?:'ve| have)?\s+cancel+ed|has been cancel+ed|is now cancel+ed|was cancel+ed)\b"


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n", text) if s.strip()]


def _affirmed(text: str, pattern: str) -> bool:
    """pattern appears in a sentence with no negation word (negation inside the match does not count)."""
    return any(re.search(pattern, s, re.I) and not NEGATION.search(re.sub(pattern, " ", s, flags=re.I))
               for s in _sentences(text))


def _done(records: list[ToolRecord], name: str) -> bool:
    return any(r.name == name and r.ok and not r.blocked for r in records)


def validate_response(final: FinalAnswer, *, question: str, passages: list[Passage],
                      tool_records: list[ToolRecord], actor_id: str, sources: str = "",
                      needs_citation: bool = True) -> Validation:
    """Check ``final`` against what was retrieved and what the tools returned.

    ``sources`` is the text the answer was built from (passages + tool results); ``needs_citation`` is
    False for an answer built only from the employee's own HR data."""
    out = Validation()
    text = final.answer
    tools_text = json.dumps([r.result for r in tool_records if not r.blocked], ensure_ascii=False, default=str)
    known = f"{question}\n{tools_text}\n{sources}"

    # ---- block: safety --------------------------------------------------
    for kind, pattern in CLAIMS.items():
        status = "APPROVED" if kind == "approved" else "PENDING"
        if _affirmed(text, pattern) and status not in tools_text:
            out.issues.append(Issue("approval_claim", "block", f"claims leave was {kind}; no tool result shows it"))
            break
    others = sorted({m for m in EMP_ID.findall(text) if m != actor_id and m not in known})
    leaked = [e for e in EMAIL.findall(text) if e not in known and not e.endswith(".invalid")]
    longs = [d for d in LONG_DIGITS.findall(text) if d not in known]
    if others or leaked or longs:
        out.issues.append(Issue("other_employee", "block",
                                "figures or identifiers not in the question or tool results: "
                                + ", ".join(others + leaked + longs)[:120]))
    if SECRET.search(text) or PROMPT_TEXT.search(text):
        out.issues.append(Issue("secret_leak", "block", "key-like string or prompt text in the answer"))
    if final.behaviour == "refuse_pii" and (MONEY.search(text) or LONG_DIGITS.search(text)):
        out.issues.append(Issue("pii_figure", "block", "a money amount or long number in a PII refusal"))

    # ---- fix: repairable by one revision ----------------------------------
    problems = render.check_citations(final, passages)
    if not needs_citation:
        problems = [p for p in problems if "needs at least one citation" not in p]
    for p in problems:
        out.issues.append(Issue("citation", "fix", p))
    for n in render.unsupported_numbers(text, sources, question):
        out.issues.append(Issue("number", "fix", f"the number {n} is not in the passages, tool results or question"))
    if (final.behaviour == "draft_for_confirmation" or _affirmed(text, DRAFT_CLAIM)) \
            and not _done(tool_records, "create_leave_draft"):
        out.issues.append(Issue("draft_claim", "fix", "the answer says a draft was saved, but no draft was created"))
    if _affirmed(text, CANCEL_CLAIM) and not _done(tool_records, "cancel_leave_request"):
        out.issues.append(Issue("cancel_claim", "fix", "the answer says a request was cancelled, but no cancellation was made"))
    return out

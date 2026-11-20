"""Typed outputs of the prompts. They are passed to ``llm.client.chat(schema=...)``,
so the server enforces the shape and the client validates it."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Route = Literal["policy", "hrms", "policy_and_hrms", "clarify",
                "refuse_injection", "refuse_pii", "escalate", "out_of_scope"]
Behaviour = Literal["answer", "refuse_not_in_corpus", "clarify", "flag_conflict", "refuse_injection",
                    "refuse_pii", "escalate_to_human", "draft_for_confirmation"]


class Plan(BaseModel):
    route: Route
    reason: str = Field(description="One sentence: why this route.")
    steps: list[str] = Field(default_factory=list, max_length=4,
                             description="Searches to run or HRMS tools to call, in order.")


class CitationRef(BaseModel):
    passage_id: str = Field(description="The id in square brackets before the passage.")
    quote: str = Field(description="Words copied exactly from that passage.")


class FinalAnswer(BaseModel):
    behaviour: Behaviour
    answer: str
    citations: list[CitationRef] = Field(default_factory=list)


class Critique(BaseModel):
    verdict: Literal["ok", "revise"]
    issues: list[str] = Field(default_factory=list, max_length=4)

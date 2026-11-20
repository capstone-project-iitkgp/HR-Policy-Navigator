"""Shared data contracts (Day-1 interfaces; see plan.html section 4).

Everything that crosses a boundary between knowledge (Akash) and agent (Himadri)
lives here: retrieval returns ``Passage``, the LLM client returns ``ChatResult``,
the API takes ``AgentRequest`` and returns ``AgentResponse``.
Change a field only after telling the other person.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Passage(BaseModel):
    """One retrieved chunk, ready to cite."""

    doc_id: str
    chunk_id: str
    text: str
    title: str = ""
    org: str = ""
    jurisdiction: str = ""
    category: str = ""
    url: str = ""
    version_date: str = ""  # ISO date of the policy version, "" if unknown
    synthetic: bool = False  # True for the labelled fictional-company partition
    # Filled in as the pipeline runs: keys "bm25", "dense", "rrf", "rerank".
    scores: dict[str, float] = Field(default_factory=dict)


class Usage(BaseModel):
    """Token counts, summed over every LLM call that produced a result."""

    tokens_in: int = 0
    tokens_out: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(tokens_in=self.tokens_in + other.tokens_in,
                     tokens_out=self.tokens_out + other.tokens_out)


class ChatResult(BaseModel):
    """What ``llm.client.chat`` returns."""

    text: str  # model output with <think> blocks removed
    parsed: Any = None  # validated schema object when a schema was given, else None
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0  # wall time of the call(s); 0 on a cache hit
    cached: bool = False
    finish_reason: str = ""  # "stop", or "length" if the output was cut off
    json_valid: bool = True  # False if a schema was given and validation failed
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)  # raw OpenAI tool_calls from the message


class Citation(BaseModel):
    doc_id: str
    chunk_id: str = ""
    section: str = ""
    quote: str = ""  # passage text the answer relies on


class AgentRequest(BaseModel):
    question: str
    session_id: str
    employee_id: str | None = None  # set by the UI persona login, never by the model


class AgentResponse(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)  # trace of plan/act/critic steps
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)  # {name, args, error}
    status: Literal["answered", "refused", "needs_confirmation", "error"] = "answered"
    usage: Usage = Field(default_factory=Usage)

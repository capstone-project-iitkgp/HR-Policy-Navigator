"""Run record: one question x one variant x one repeat. Format: evaluation/run_record_schema.md.

The scorer reads these as plain dicts, so extra fields never break it. The settings at the
bottom are what the Eval Playbook asks us to log with every run. Never put a key or real
personal data in a record.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Citation(BaseModel):
    doc_id: str = ""
    section: str = ""
    quote: str = ""  # passage text the answer claims as support


class RetrievedChunk(BaseModel):
    chunk_id: str = ""
    doc_id: str = ""
    text: str = ""  # log what the tool returned, in rank order, not what the LLM used


class ToolCall(BaseModel):
    name: str
    args: dict = Field(default_factory=dict)
    error: str | None = None


class RunRecord(BaseModel):
    qid: str
    variant: str
    repeat: int = 0
    answer_text: str = ""  # final answer, thinking text stripped
    citations: list[Citation] = Field(default_factory=list)
    retrieved: list[RetrievedChunk] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    steps: int = 0
    latency_s: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    finish_reason: str = ""  # "length" means truncated
    json_valid: bool = True  # did every structured output validate after retries?
    error: str | None = None  # unrecovered exception, loop abort or step-cap abort
    # run settings
    model: str = ""
    temperature: float | None = None
    seed: int | None = None
    prompt_hash: str = ""
    eval_name: str = ""
    eval_sha: str = ""
    git_commit: str = ""
    date: str = ""
    tag: str = ""  # "mock" when the run used the local stand-in model: never report those numbers

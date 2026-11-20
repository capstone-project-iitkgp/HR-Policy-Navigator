"""What the agent knows during one question. Plain data plus a few small helpers;
the orchestrator (not this file) decides what to do next."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from hr_navigator.prompts.schemas import Plan
from hr_navigator.schemas import Passage, Usage

STEP_CAP = 10  # plan decision 1: any planner/executor/tool/revision/critic action is one step


def call_key(name: str, args: dict[str, Any]) -> str:
    """Same tool and same arguments give the same key (used to catch repeated calls)."""
    return name + ":" + json.dumps(args, sort_keys=True, ensure_ascii=False)


class ToolRecord(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    result: Any = None  # the tool's dict; on failure {"error": "..."}
    blocked: bool = False  # True if not executed because it repeated an earlier call


class AgentState(BaseModel):
    question: str
    employee_id: str = ""
    profile: dict[str, Any] = Field(default_factory=dict)
    plan: Plan | None = None
    passages: list[Passage] = Field(default_factory=list)  # best first, unique by chunk_id
    tool_records: list[ToolRecord] = Field(default_factory=list)
    code_tools: list[ToolRecord] = Field(default_factory=list)  # called by code (validate, escalate), never shown to the model
    seen_calls: set[str] = Field(default_factory=set)
    steps: int = 0
    trace: list[str] = Field(default_factory=list)  # one short line per step, goes to AgentResponse.steps
    usage: Usage = Field(default_factory=Usage)
    final_behaviour: str = ""  # behaviour of the final answer, "" if there was none
    llm_calls: int = 0  # model calls that returned (H9 cost table)
    cache_hits: int = 0  # of those, answered from the disk cache (their latency is not real)
    llm_ms: int = 0  # summed latency the endpoint reported for the uncached calls

    def step(self, note: str) -> None:
        """Count one step and log it."""
        self.steps += 1
        self.trace.append(f"{self.steps}. {note}")

    def note(self, text: str) -> None:
        """Log something that is not a step (a fallback, a trim)."""
        self.trace.append(f"- {text}")

    @property
    def at_cap(self) -> bool:
        return self.steps >= STEP_CAP

    def add_passages(self, new: list[Passage]) -> int:
        """Append passages not yet held; returns how many were new."""
        have = {p.chunk_id for p in self.passages}
        fresh = []
        for p in new:
            if p.chunk_id not in have:
                have.add(p.chunk_id)
                fresh.append(p)
        self.passages.extend(fresh)
        return len(fresh)

    def is_repeat(self, name: str, args: dict[str, Any]) -> bool:
        return call_key(name, args) in self.seen_calls

    def record_tool(self, name: str, args: dict[str, Any], result: Any, *,
                    ok: bool = True, blocked: bool = False) -> None:
        self.seen_calls.add(call_key(name, args))
        self.tool_records.append(ToolRecord(name=name, args=args, ok=ok, result=result, blocked=blocked))

    def record_code_tool(self, name: str, args: dict[str, Any], result: Any, *, ok: bool = True) -> None:
        self.code_tools.append(ToolRecord(name=name, args=args, ok=ok, result=result))

    def gathered_text(self) -> str:
        """Short summary for the executor prompt ({{gathered}})."""
        lines = [f"- {r.name}({json.dumps(r.args, ensure_ascii=False)}): "
                 + ("blocked, repeated call" if r.blocked else ("ok" if r.ok else "failed"))
                 for r in self.tool_records]
        lines.append(f"- passages found so far: {len(self.passages)}")
        return "\n".join(lines)

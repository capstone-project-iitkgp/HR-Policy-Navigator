"""v2_rag and v3_agent: the retrieval-backed variants.

v2_rag              search once by code, answer prompt, citation check (no planner, no tools loop)
v3_agent            planner -> tool loop -> answer -> checks, critic advisory (logged only)
v3_agent_nocritic   same with the critic off, for the H9 ablation

They need ``search(query, k, filters) -> list[Passage]`` from retrieval (Akash). Until
``hr_navigator.retrieval.search`` exists, call ``set_search`` (tests and the dev run do).
Capacity and network errors stop the run (nothing recorded) as for the v0 variants.
Note for H9: hrms_ext items write drafts into the HRMS database; reset it between repeats.
"""

from __future__ import annotations

import importlib
import os
import time
from typing import Callable

from evaluation.baselines import _model, _prompt_hash
from evaluation.records import Citation, RetrievedChunk, RunRecord, ToolCall
from evaluation.variants import register
from hr_navigator import prompts
from hr_navigator.agents.orchestrator import Agent
from hr_navigator.agents.toolbox import ToolBox
from hr_navigator.schemas import AgentRequest, AgentResponse, Passage

TEMPERATURE = 0.2  # seed = repeat number, as for the baselines
_search: Callable[..., list[Passage]] | None = None


def set_search(fn: Callable[..., list[Passage]] | None) -> None:
    global _search
    _search = fn


def get_search() -> Callable[..., list[Passage]]:
    if _search is not None:
        return _search
    try:
        fn = getattr(importlib.import_module("hr_navigator.retrieval"), "search", None)
    except ImportError:
        fn = None
    if fn is None:
        # SystemExit is not an Exception, so the runner stops instead of writing 240 error records.
        raise SystemExit("v2_rag / v3_agent need retrieval: hr_navigator.retrieval.search does not exist yet "
                         "(Akash's part). Run `python -m evaluation.dev_run --agent` to try the loop on dev items.")
    return fn


def profile_from_item(item: dict) -> dict:
    """Eval items carry their own employee profile; HRMS items use the HR system's instead."""
    ctx = item.get("employee_context") or {}
    return {k: v for k, v in ctx.items() if v and k != "name_alias"}


def _agent(critic: str) -> Agent:
    return Agent(ToolBox(get_search()), critic=critic, raise_http_errors=True, temperature=TEMPERATURE)


def to_record(item: dict, variant: str, repeat: int, response: AgentResponse, state, started: float,
              prompt_names: tuple[str, ...], config: str) -> RunRecord:
    invalid = any("invalid JSON twice" in line for line in state.trace)
    return RunRecord(
        qid=item["qid"], variant=variant, repeat=repeat, answer_text=response.answer,
        citations=[Citation(doc_id=c.doc_id, section=c.section, quote=c.quote) for c in response.citations],
        retrieved=[RetrievedChunk(chunk_id=p.chunk_id, doc_id=p.doc_id, text=p.text) for p in state.passages],
        tool_calls=[ToolCall(name=t["name"], args=t["args"], error=t["error"]) for t in response.tool_calls],
        steps=state.steps, latency_s=round(time.perf_counter() - started, 3),
        tokens_in=response.usage.tokens_in, tokens_out=response.usage.tokens_out,
        json_valid=not invalid, error="answer stage failed (invalid JSON)" if invalid else None,
        model=_model(), temperature=TEMPERATURE, seed=repeat,
        prompt_hash=_prompt_hash(*(prompts.load(n).hash for n in prompt_names), config))


def _run(item: dict, variant: str, repeat: int, critic: str, rag: bool) -> RunRecord:
    started = time.perf_counter()
    agent = _agent(critic)
    if item.get("today"):
        os.environ["HRMS_TODAY"] = item["today"]
    request = AgentRequest(question=item["question"], session_id="", employee_id=item.get("acting_employee"))
    profile = None if item.get("acting_employee") else profile_from_item(item)
    response, state = agent.run_with_state(request, seed=repeat, profile=profile, rag=rag)
    names = ("answer",) if rag else ("planner", "executor", "answer", "critic")
    return to_record(item, variant, repeat, response, state, started, names,
                     f"critic={critic};strict={agent.strict_citations}")


@register("v2_rag")
def v2_rag(item: dict, repeat: int) -> RunRecord:
    return _run(item, "v2_rag", repeat, "off", rag=True)


@register("v3_agent")
def v3_agent(item: dict, repeat: int) -> RunRecord:
    return _run(item, "v3_agent", repeat, "advisory", rag=False)


@register("v3_agent_nocritic")
def v3_agent_nocritic(item: dict, repeat: int) -> RunRecord:
    return _run(item, "v3_agent_nocritic", repeat, "off", rag=False)

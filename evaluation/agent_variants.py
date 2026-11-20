"""v2_rag and v3_agent: the retrieval-backed variants.

v2_rag              search once by code, answer prompt, citation check (no planner, no tools loop)
v3_agent            planner -> tool loop -> answer -> checks, critic advisory (logged only)
v3_agent_nocritic   same with the critic off, for the H9 ablation

They need ``search(query, k, filters) -> list[Passage]`` from retrieval (Akash). Until
``hr_navigator.retrieval.search`` exists, call ``set_search`` (tests and the dev run do).
Capacity and network errors stop the run (nothing recorded) as for the v0 variants.
H9 ablations: v3_agent_lenient (strict_citations off), v3_agent_noblock (validate_response only reports),
v3_agent_revise (critic may trigger one revision).

Every item with an acting employee runs on its own fresh copy of the seeded HRMS database
(``isolated_hrms``), so drafts made by one question or repeat never change another's result.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
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


def _agent(critic: str, **options) -> Agent:
    return Agent(ToolBox(get_search()), critic=critic, raise_http_errors=True, temperature=TEMPERATURE, **options)


_template: Path | None = None


@contextlib.contextmanager
def isolated_hrms(today: str | None = None):
    """A fresh seeded HRMS database (and HRMS_TODAY) for one run; the environment is restored after."""
    global _template
    from hrms import seed
    if _template is None or not _template.is_file():
        _template = Path(tempfile.mkdtemp(prefix="h9_hrms_")) / "template.db"
        saved = os.environ.pop("HRMS_TODAY", None)  # seed with the default date, not a leftover one
        try:
            seed.build(_template)
        finally:
            if saved is not None:
                os.environ["HRMS_TODAY"] = saved
    work = Path(tempfile.mkdtemp(prefix="h9_run_")) / "hrms.db"
    shutil.copyfile(_template, work)
    old = {k: os.environ.get(k) for k in ("HRMS_DB_PATH", "HRMS_TODAY")}
    os.environ["HRMS_DB_PATH"] = str(work)
    if today:
        os.environ["HRMS_TODAY"] = today
    try:
        yield work
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(work.parent, ignore_errors=True)


def agent_facts(response: AgentResponse, state) -> dict:
    """What the checks and the model did, for the H9 tables (taxonomy, block counts, cost)."""
    validate = [t for t in state.code_tools if t.name == "validate_response"]
    codes = list((validate[-1].result or {}).get("issues", [])) if validate else []
    blocks = [line for line in state.trace if "validate_response: blocked" in line]
    verdict = next((m.group(1) for line in state.trace
                    if (m := re.search(r"critic -> (\w+)", line))), "")
    return {"behaviour": state.final_behaviour, "status": response.status, "issues": codes,
            "blocked": bool(blocks) and not blocks[-1].endswith("[report only]"),
            "report_only_block": bool(blocks) and blocks[-1].endswith("[report only]"),
            "escalated": any(t.name == "escalate_to_hr" and t.ok for t in state.code_tools),
            "revisions": sum("revision (" in line for line in state.trace),
            "critic_verdict": verdict, "llm_calls": state.llm_calls, "cache_hits": state.cache_hits,
            "llm_ms": state.llm_ms}


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
        agent=agent_facts(response, state), model=_model(), temperature=TEMPERATURE, seed=repeat,
        prompt_hash=_prompt_hash(*(prompts.load(n).hash for n in prompt_names), config))


def _run(item: dict, variant: str, repeat: int, critic: str, rag: bool, **options) -> RunRecord:
    started = time.perf_counter()
    agent = _agent(critic, **options)
    acting = item.get("acting_employee")
    with isolated_hrms(item.get("today")) if acting or item.get("today") else contextlib.nullcontext():
        request = AgentRequest(question=item["question"], session_id="", employee_id=acting)
        profile = None if acting else profile_from_item(item)
        response, state = agent.run_with_state(request, seed=repeat, profile=profile, rag=rag)
    names = ("answer",) if rag else ("planner", "executor", "answer", "critic")
    return to_record(item, variant, repeat, response, state, started, names,
                     f"critic={critic};strict={agent.strict_citations};block={agent.block_unsafe}")


@register("v2_rag")
def v2_rag(item: dict, repeat: int) -> RunRecord:
    return _run(item, "v2_rag", repeat, "off", rag=True)


@register("v3_agent")
def v3_agent(item: dict, repeat: int) -> RunRecord:
    return _run(item, "v3_agent", repeat, "advisory", rag=False)


@register("v3_agent_nocritic")
def v3_agent_nocritic(item: dict, repeat: int) -> RunRecord:
    return _run(item, "v3_agent_nocritic", repeat, "off", rag=False)


@register("v3_agent_lenient")
def v3_agent_lenient(item: dict, repeat: int) -> RunRecord:
    """Ablation: citations that do not verify are kept (strict_citations off)."""
    return _run(item, "v3_agent_lenient", repeat, "advisory", rag=False, strict_citations=False)


@register("v3_agent_noblock")
def v3_agent_noblock(item: dict, repeat: int) -> RunRecord:
    """Ablation: validate_response reports unsafe answers but does not replace them."""
    return _run(item, "v3_agent_noblock", repeat, "advisory", rag=False, block_unsafe=False)


@register("v3_agent_revise")
def v3_agent_revise(item: dict, repeat: int) -> RunRecord:
    """Ablation: the critic may ask for one revision."""
    return _run(item, "v3_agent_revise", repeat, "revise", rag=False)

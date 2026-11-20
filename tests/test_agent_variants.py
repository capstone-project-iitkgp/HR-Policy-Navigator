import sys

import pytest

from evaluation import agent_variants, dev_run
from evaluation.variants import VARIANTS
from hr_navigator.agents import orchestrator
from hr_navigator.llm.http import LLMCapacityError
from hr_navigator.prompts.schemas import Critique, FinalAnswer, Plan
from tests.fake_search import CASUAL, CLAIMS, make_search
from tests.test_orchestrator import PLAN, QUOTE, REFUSE, SEARCH, Script, answer, schema_error, tool_call

ITEM = {"qid": "Q900", "question": "How many casual leave days do I get?",
        "employee_context": {"name_alias": "EMP-1", "org": "Zeta", "role": "Analyst"}}


@pytest.fixture(autouse=True)
def search(monkeypatch, tmp_path):
    monkeypatch.setenv("HRMS_DB_PATH", str(tmp_path / "h.db"))
    agent_variants.set_search(make_search([CASUAL, CLAIMS]))
    yield
    agent_variants.set_search(None)


def use(monkeypatch, *items):
    script = Script(*items)
    monkeypatch.setattr(orchestrator, "chat", script)
    return script


def test_variants_are_registered():
    assert {"v2_rag", "v3_agent", "v3_agent_nocritic"} <= set(VARIANTS)


def test_v3_agent_record_has_the_run_fields(monkeypatch):
    script = use(monkeypatch, PLAN, SEARCH, "DONE", answer(), Critique(verdict="ok"))
    rec = VARIANTS["v3_agent"](ITEM, 2)
    assert rec.variant == "v3_agent" and rec.repeat == 2 and rec.seed == 2 and rec.temperature == 0.2
    assert "12 casual" in rec.answer_text and rec.citations[0].doc_id == "zeta-leave"
    assert rec.citations[0].quote == QUOTE
    assert [r.chunk_id for r in rec.retrieved] == ["z-1"] and rec.tool_calls[0].name == "policy_search"
    assert rec.steps == 5 and rec.tokens_in == 500 and rec.json_valid and rec.error is None
    assert len(rec.prompt_hash) == 12 and script.calls[0]["seed"] == 2
    assert "Zeta" in script.calls[0]["messages"][1]["content"] and "EMP-1" not in script.calls[0]["messages"][1]["content"]


def test_nocritic_makes_no_critic_call_and_changes_the_hash(monkeypatch):
    use(monkeypatch, PLAN, SEARCH, "DONE", answer(), Critique(verdict="ok"))
    with_critic = VARIANTS["v3_agent"](ITEM, 0)
    script = use(monkeypatch, PLAN, SEARCH, "DONE", answer())
    without = VARIANTS["v3_agent_nocritic"](ITEM, 0)
    assert len(script.calls) == 4 and without.prompt_hash != with_critic.prompt_hash


def test_v2_rag_searches_once_by_code_and_skips_planner(monkeypatch):
    script = use(monkeypatch, answer())
    rec = VARIANTS["v2_rag"](ITEM, 0)
    assert len(script.calls) == 1 and script.calls[0]["tag"] == "agent_answer"
    assert [t.name for t in rec.tool_calls] == ["policy_search", "validate_response"] and rec.retrieved[0].chunk_id == "z-1"
    assert rec.steps == 1 and rec.citations


def test_invalid_answer_json_is_a_scored_failure_not_a_crash(monkeypatch):
    use(monkeypatch, PLAN, SEARCH, "DONE", schema_error())
    rec = VARIANTS["v3_agent"](ITEM, 0)
    assert rec.json_valid is False and rec.error


def test_capacity_error_propagates_so_the_runner_stops(monkeypatch):
    use(monkeypatch, LLMCapacityError(429, "busy"))
    with pytest.raises(LLMCapacityError):
        VARIANTS["v3_agent"](ITEM, 0)


def test_missing_retrieval_stops_the_run_with_a_clear_message(monkeypatch):
    agent_variants.set_search(None)
    monkeypatch.setitem(sys.modules, "hr_navigator.retrieval", None)  # retrieval exists now; hide it
    with pytest.raises(SystemExit, match="retrieval"):
        VARIANTS["v3_agent"](ITEM, 0)


def test_hrms_item_uses_the_acting_employee_and_hr_profile(monkeypatch):
    item = {"qid": "H900", "question": "How many leave days do I have?", "acting_employee": "D010",
            "today": "2026-11-16", "employee_context": {"org": "DEF"}}
    script = use(monkeypatch, Plan(route="hrms", reason="r"), tool_call("get_leave_balance", {}), "DONE", REFUSE,
                 Critique(verdict="ok"))
    rec = VARIANTS["v3_agent"](item, 0)
    assert rec.tool_calls[0].name == "get_leave_balance" and rec.tool_calls[0].error is None
    assert "policy_org" in script.calls[0]["messages"][1]["content"]  # HR-system profile, not the item's


def test_dev_run_agent_mode(monkeypatch):
    item = {"qid": "D99", "question": "How many casual leave days do I get?", "expected_behaviour": "answer",
            "expected_route": "policy", "must_include": ["12"], "employee_context": {"org": "Zeta"},
            "passages": [CASUAL.model_dump()]}
    use(monkeypatch, PLAN, SEARCH, "DONE", answer(), Critique(verdict="ok"))
    row = dev_run.run_item_agent(item)
    assert row["behaviour"] == "answer" and row["route_ok"] and row["facts_ok"] and row["behaviour_ok"]
    assert row["citation_problems"] == [] and row["tool_calls"] == ["policy_search", "validate_response"] and row["critic"] == "ok"

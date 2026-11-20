"""H7 step 4: drive the trace page headlessly (streamlit.testing AppTest)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from hr_navigator.llm.http import LLMCapacityError
from hr_navigator.observability import summary
from hr_navigator.observability.tracing import JsonlSink, Tracer
from hr_navigator.prompts.schemas import Critique, FinalAnswer, Plan
from hr_navigator.schemas import AgentRequest
from tests.test_orchestrator import PLAN, Script, answer, hrms_env, make_agent, tool_call  # noqa: F401

APP = str(Path(__file__).resolve().parents[1] / "app" / "trace_page.py")


def ask(script, monkeypatch, tmp_path, question="How many casual leave days do I get?"):
    agent = make_agent(script, monkeypatch, tracer=Tracer(JsonlSink(tmp_path)))
    return agent.run(AgentRequest(question=question, session_id="s1", employee_id="D010"))


def open_page(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    return at


def texts(elements):
    return " ".join(e.value for e in elements)


def test_empty_folder_shows_a_hint(tmp_path, monkeypatch):
    at = open_page(tmp_path / "none", monkeypatch)
    assert "No traces yet" in texts(at.info)


def test_lists_traces_and_shows_the_selected_one(tmp_path, monkeypatch):
    ask(Script(PLAN, tool_call("policy_search", {"query": "casual leave"}), "DONE", answer(), Critique(verdict="ok")),
        monkeypatch, tmp_path)
    ask(Script(Plan(route="refuse_pii", reason="r"), FinalAnswer(behaviour="refuse_pii", answer="No."),
               Critique(verdict="ok")), monkeypatch, tmp_path, "What is Asha's salary?")
    at = open_page(tmp_path, monkeypatch)
    table = at.dataframe[0].value
    assert len(table) == 2 and set(table["Status"]) == {"answered", "refused"}
    # the newest trace (the refusal) is selected first
    assert "What is Asha's salary?" in texts(at.markdown) and "refuse_pii" in texts(at.markdown)
    names = " ".join(e.label for e in at.expander)
    assert "agent.run" in names and "escalate_to_hr" in names and "tool policy_search" not in names
    assert at.metric[2].value.startswith("3")  # planner, answer, critic


def test_choosing_another_trace_changes_the_detail(tmp_path, monkeypatch):
    ask(Script(PLAN, tool_call("policy_search", {"query": "casual leave"}), "DONE", answer(), Critique(verdict="ok")),
        monkeypatch, tmp_path)
    ask(Script(Plan(route="refuse_pii", reason="r"), FinalAnswer(behaviour="refuse_pii", answer="No."),
               Critique(verdict="ok")), monkeypatch, tmp_path, "What is Asha's salary?")
    at = open_page(tmp_path, monkeypatch)
    options = at.selectbox(key="trace").options
    older = next(o for o in options if "casual leave days" in o)
    at.selectbox(key="trace").select(older).run()
    assert not at.exception and at.metric[2].value.startswith("5")
    assert any("tool policy_search" in e.label for e in at.expander)


def test_errors_only_filter_and_error_marking(tmp_path, monkeypatch):
    ask(Script(PLAN, tool_call("policy_search", {"query": "casual leave"}), "DONE", answer(), Critique(verdict="ok")),
        monkeypatch, tmp_path)
    ask(Script(LLMCapacityError(429, "busy")), monkeypatch, tmp_path)
    at = open_page(tmp_path, monkeypatch)
    assert len(at.dataframe[0].value) == 2
    at.checkbox(key="errors_only").set_value(True).run()
    assert not at.exception
    table = at.dataframe[0].value
    assert len(table) == 1 and table["Status"].tolist() == ["error"]
    assert any(e.label.strip().startswith("ERROR") for e in at.expander)


def test_page_reads_what_summary_reads(tmp_path, monkeypatch):
    ask(Script(PLAN, tool_call("policy_search", {"query": "casual leave"}), "DONE", answer(), Critique(verdict="ok")),
        monkeypatch, tmp_path)
    at = open_page(tmp_path, monkeypatch)
    s = next(iter(summary.by_trace(summary.load(tmp_path)).values()))
    assert at.dataframe[0].value["Tokens"].tolist() == [summary.summarize(s)["tokens_in"] + summary.summarize(s)["tokens_out"]]


def test_span_rows_order_and_depth(tmp_path, monkeypatch):
    ask(Script(PLAN, tool_call("policy_search", {"query": "casual leave"}), "DONE", answer(), Critique(verdict="ok")),
        monkeypatch, tmp_path)
    rows = summary.span_rows(next(iter(summary.by_trace(summary.load(tmp_path)).values())))
    assert (rows[0]["depth"], rows[0]["name"]) == (0, "agent.run")
    assert (1, "plan") in [(r["depth"], r["name"]) for r in rows]
    assert (2, "llm agent_planner") in [(r["depth"], r["name"]) for r in rows]
    assert all(r["duration_ms"] >= 0 for r in rows)

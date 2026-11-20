"""H7 step 3: reading traces back and summarising them."""

import json

from hr_navigator.observability import summary
from hr_navigator.observability.tracing import JsonlSink, Tracer
from hr_navigator.prompts.schemas import Critique, FinalAnswer, Plan
from hr_navigator.schemas import AgentRequest
from tests.test_orchestrator import PLAN, Script, answer, hrms_env, make_agent, tool_call  # noqa: F401


def run(script, monkeypatch, tmp_path, question="How many casual leave days do I get?"):
    tracer = Tracer(JsonlSink(tmp_path))
    agent = make_agent(script, monkeypatch, tracer=tracer)
    resp = agent.run(AgentRequest(question=question, session_id="s1", employee_id="D010"))
    return resp, tracer


def test_summary_of_a_policy_question(monkeypatch, tmp_path):
    script = Script(PLAN, tool_call("policy_search", {"query": "casual leave"}), "DONE", answer(), Critique(verdict="ok"))
    resp, tracer = run(script, monkeypatch, tmp_path)
    traces = summary.by_trace(summary.load(tmp_path))
    assert list(traces) == [tracer.last_trace_id]
    s = summary.summarize(traces[tracer.last_trace_id])
    assert s["root"] and s["status"] == "answered" and s["behaviour"] == "answer" and s["employee"] == "D010"
    assert s["llm_calls"] == 5 and s["tool_calls"] == 1 and s["blocked_tool_calls"] == 0 and s["errors"] == 0
    assert s["tokens_in"] == resp.usage.tokens_in and s["tokens_out"] == resp.usage.tokens_out
    assert {"plan", "executor", "answer", "validate_response", "critic"} <= set(s["stage_ms"])
    assert "escalate_to_hr" not in s["stage_ms"]
    assert s["latency_ms"] >= sum(s["stage_ms"].values()) * 0.99
    text = summary.format_summary(s)
    assert s["trace_id"] in text and "model calls 5" in text and "executor" in text


def test_two_questions_make_two_traces_and_errors_are_counted(monkeypatch, tmp_path):
    from hr_navigator.llm.http import LLMCapacityError
    run(Script(LLMCapacityError(429, "busy")), monkeypatch, tmp_path)
    run(Script(Plan(route="refuse_pii", reason="r"), FinalAnswer(behaviour="refuse_pii", answer="No."),
               Critique(verdict="ok")), monkeypatch, tmp_path)
    sums = [summary.summarize(t) for t in summary.by_trace(summary.load(tmp_path)).values()]
    assert [x["status"] for x in sums] == ["error", "refused"]
    assert sums[0]["errors"] >= 2 and sums[1]["errors"] == 0
    assert "escalate_to_hr" in sums[1]["stage_ms"] and sums[1]["tool_calls"] == 0  # code-called tools are not counted


def test_load_skips_bad_lines_and_missing_files(tmp_path):
    (tmp_path / "2026-11-20.jsonl").write_text("not json\n" + json.dumps(
        {"trace_id": "t", "span_id": "s", "parent_id": None, "name": "agent.run", "start_ns": 1, "end_ns": 2_000_001,
         "status": "ok", "attributes": {}}) + "\n")
    assert len(summary.load(tmp_path)) == 1
    assert summary.load(tmp_path, day="2026-01-01") == []
    assert summary.load(tmp_path / "nope") == []


def test_summarize_a_trace_without_a_root():
    s = summary.summarize([{"trace_id": "t", "span_id": "a", "parent_id": "gone", "name": "plan", "start_ns": 0,
                            "end_ns": 5, "status": "ok", "attributes": {}}])
    assert s["root"] is False and s["latency_ms"] == 0.0 and s["llm_calls"] == 0
    assert summary.summarize([])["trace_id"] == ""

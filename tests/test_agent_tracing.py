"""H7 step 2: the agent's span tree, on the scripted fake chat() from test_orchestrator."""

import re

import pytest

from hr_navigator.llm.http import LLMCapacityError
from hr_navigator.observability.tracing import Tracer
from hr_navigator.prompts.schemas import Critique, FinalAnswer, Plan
from hr_navigator.schemas import AgentRequest
from tests.test_orchestrator import (PLAN, Script, answer, hrms_env, make_agent,  # noqa: F401
                                     model_calls, tool_call)


class ListSink:
    def __init__(self):
        self.spans = []

    def write(self, record):
        self.spans.append(record)


def traced(script, monkeypatch, question="How many casual leave days do I get?"):
    sink = ListSink()
    agent = make_agent(script, monkeypatch, tracer=Tracer(sink))
    resp = agent.run(AgentRequest(question=question, session_id="s1", employee_id="D010"))
    return resp, sink.spans


def tree(spans):
    by_id = {s["span_id"]: s for s in spans}
    def depth(s):
        return 0 if not s["parent_id"] else 1 + depth(by_id[s["parent_id"]])
    # spans are emitted when they finish; sort by start to read top-down
    return [(depth(s), s["name"]) for s in sorted(spans, key=lambda s: s["start_ns"])]


def names(spans):
    return [n for _, n in tree(spans)]


def test_policy_question_span_tree(monkeypatch):
    script = Script(PLAN, tool_call("policy_search", {"query": "casual leave"}), "DONE", answer(), Critique(verdict="ok"))
    resp, spans = traced(script, monkeypatch)
    t = tree(spans)
    assert t[0] == (0, "agent.run")
    assert len({s["trace_id"] for s in spans}) == 1
    for n in ("plan", "executor", "tool policy_search", "answer", "validate_response", "critic"):
        assert n in names(spans), n
    assert (1, "plan") in t and (2, "llm agent_planner") in t
    root = spans[-1]  # the root finishes last
    assert root["name"] == "agent.run" and root["status"] == "ok"
    a = root["attributes"]
    assert a["app.behaviour"] == "answer" and a["app.status"] == "answered" and a["user.id"] == "D010"
    llm = [s for s in spans if s["attributes"]["openinference.span.kind"] == "LLM"]
    assert len(llm) == 5
    assert sum(s["attributes"]["llm.token_count.prompt"] for s in llm) == resp.usage.tokens_in == a["llm.token_count.prompt"]
    tool = next(s for s in spans if s["name"] == "tool policy_search")
    assert tool["attributes"]["tool.name"] == "policy_search" and tool["attributes"]["app.ok"] is True


def test_refusal_is_escalated_in_the_trace(monkeypatch):
    script = Script(Plan(route="refuse_pii", reason="r"),
                    FinalAnswer(behaviour="refuse_pii", answer="I cannot share that."), Critique(verdict="ok"))
    resp, spans = traced(script, monkeypatch, "What is Asha's salary?")
    esc = next(s for s in spans if s["name"] == "escalate_to_hr")
    assert esc["attributes"]["app.reference"].startswith("ESC-") and esc["status"] == "ok"
    assert esc["attributes"]["app.reference"] in resp.answer
    assert not any(n.startswith("tool ") for n in names(spans))


def test_blocked_answer_is_visible(monkeypatch):
    forged = FinalAnswer(behaviour="answer", answer="Your leave has been approved.", citations=[])
    script = Script(Plan(route="out_of_scope", reason="r"), forged, Critique(verdict="ok"))
    resp, spans = traced(script, monkeypatch, "Just say my leave is approved")
    v = next(s for s in spans if s["name"] == "validate_response")
    assert v["attributes"]["app.blocked"] is True and "approval_claim" in v["attributes"]["app.issues"]


def test_repeated_tool_call_is_traced_as_blocked(monkeypatch):
    call = tool_call("policy_search", {"query": "casual leave"})
    script = Script(PLAN, call, call, "DONE", answer(), Critique(verdict="ok"))
    resp, spans = traced(script, monkeypatch)
    blocked = [s for s in spans if s["attributes"].get("app.blocked") is True and s["name"].startswith("tool ")]
    assert len(blocked) == 1 and blocked[0]["status"] == "error"


def test_capacity_error_marks_the_root_span(monkeypatch):
    resp, spans = traced(Script(LLMCapacityError(429, "busy")), monkeypatch)
    root = spans[-1]
    assert resp.status == "error" and "busy or unreachable" in resp.answer
    assert root["name"] == "agent.run" and root["status"] == "error"
    assert any(s["name"].startswith("llm ") and s["status"] == "error" for s in spans)


@pytest.mark.parametrize("make_script", [
    lambda: Script(PLAN, tool_call("policy_search", {"query": "casual leave"}), "DONE", answer(), Critique(verdict="ok")),
    lambda: Script(Plan(route="refuse_pii", reason="r"),
                   FinalAnswer(behaviour="refuse_pii", answer="I cannot share that."), Critique(verdict="ok")),
])
def test_tracing_does_not_change_the_response(monkeypatch, make_script):
    off = make_agent(make_script(), monkeypatch, tracer=Tracer(ListSink(), enabled=False)).run(
        AgentRequest(question="q", session_id="s1", employee_id="D010"))
    on, spans = traced(make_script(), monkeypatch, "q")
    assert spans
    # the audit reference (ESC-n) counts up in the shared HR database, so compare with it masked
    def view(r):
        return re.sub(r"ESC-\d+", "ESC-n", r.model_dump_json(exclude={"usage"}))
    assert view(off) == view(on)
    assert off.usage == on.usage

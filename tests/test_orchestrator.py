"""Smoke tests for the agent loop with a scripted fake chat(). The full failure-mode set is step 6."""

import json

import pytest

from hr_navigator.agents import orchestrator
from hr_navigator.agents.orchestrator import Agent
from hr_navigator.agents.toolbox import ToolBox
from hr_navigator.llm.client import SchemaError
from hr_navigator.prompts.schemas import Critique, FinalAnswer, Plan
from hr_navigator.schemas import AgentRequest, ChatResult, Usage
from tests.fake_search import CASUAL, CLAIMS, make_search

QUOTE = "Full-time employees get 12 casual leave days per year."


@pytest.fixture(autouse=True)
def hrms_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HRMS_DB_PATH", str(tmp_path / "h.db"))
    monkeypatch.setenv("HRMS_TODAY", "2026-11-16")


class Script:
    """Replaces orchestrator.chat. Items are consumed in order: a pydantic object (parsed), a str (text),
    a list of tool-call dicts, or an Exception to raise. Records every call."""

    def __init__(self, *items):
        self.items, self.calls = list(items), []

    def __call__(self, messages, *, schema=None, tools=None, tool_choice=None, **kw):
        self.calls.append({"messages": messages, "schema": schema, "tools": tools, "tool_choice": tool_choice, **kw})
        item = self.items.pop(0)
        usage = Usage(tokens_in=100, tokens_out=10)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, list):
            return ChatResult(text="", usage=usage, tool_calls=item)
        if isinstance(item, str):
            return ChatResult(text=item, usage=usage)
        return ChatResult(text=item.model_dump_json(), parsed=item, usage=usage)


def tool_call(name, args, cid="c1"):
    return [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]


def answer(**kw):
    base = dict(behaviour="answer", answer="You get 12 casual leave days a year.",
                citations=[{"passage_id": "z-1", "quote": QUOTE}])
    return FinalAnswer(**{**base, **kw})


def make_agent(script, monkeypatch, **kw):
    monkeypatch.setattr(orchestrator, "chat", script)
    return Agent(ToolBox(make_search([CASUAL, CLAIMS])), **kw)


def ask(agent, q="How many casual leave days do I get?"):
    return agent.run(AgentRequest(question=q, session_id="s1", employee_id="D010"))


def test_policy_question_end_to_end(monkeypatch):
    script = Script(Plan(route="policy", reason="r", steps=["casual leave"]),
                    tool_call("policy_search", {"query": "casual leave"}), "DONE",
                    answer(), Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch))
    assert resp.status == "answered" and "12 casual" in resp.answer
    assert resp.citations[0].chunk_id == "z-1" and resp.citations[0].doc_id == "zeta-leave"
    assert resp.tool_calls == [{"name": "policy_search", "args": {"query": "casual leave"}, "error": None}]
    assert script.calls[1]["tool_choice"] == "required" and script.calls[2]["tool_choice"] == "auto"
    assert resp.usage.tokens_in == 100 * 5
    assert any("critic -> ok" in s["note"] for s in resp.steps)


def test_refusal_route_makes_no_tool_calls_but_answers(monkeypatch):
    script = Script(Plan(route="refuse_pii", reason="r"), FinalAnswer(behaviour="refuse_pii", answer="I cannot share that."),
                    Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch), "What is Asha's salary?")
    assert resp.status == "refused" and resp.tool_calls == []


def test_capacity_error_becomes_error_status(monkeypatch):
    from hr_navigator.llm.http import LLMCapacityError
    resp = ask(make_agent(Script(LLMCapacityError(429, "busy")), monkeypatch))
    assert resp.status == "error" and "busy or unreachable" in resp.answer


def test_invented_quote_is_revised_then_dropped(monkeypatch):
    bad = answer(citations=[{"passage_id": "z-1", "quote": "Employees get 99 days."}])
    script = Script(Plan(route="policy", reason="r"), tool_call("policy_search", {"query": "casual leave"}), "DONE",
                    bad, bad, Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch))
    assert resp.status == "refused" and resp.citations == [] and "could not verify" in resp.answer

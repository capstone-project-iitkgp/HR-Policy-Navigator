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


# ---- failure modes (H5 step 6): one test per row of the plan --------------

from hr_navigator.agents.budget import CONTEXT_LIMIT, OUTPUT_RESERVE, estimate_tokens  # noqa: E402
from hr_navigator.llm.http import LLMCapacityError  # noqa: E402
from hr_navigator.schemas import Passage  # noqa: E402
from hr_navigator.state.session_store import SessionStore  # noqa: E402

PLAN = Plan(route="policy", reason="r", steps=["casual leave"])
SEARCH = tool_call("policy_search", {"query": "casual leave"})
REFUSE = FinalAnswer(behaviour="refuse_not_in_corpus", answer="The documents do not say.")


def notes(resp):
    return " | ".join(s["note"] for s in resp.steps)


def schema_error():
    return SchemaError("twice", ChatResult(text="{not json", usage=Usage(tokens_in=50, tokens_out=5), json_valid=False))


def test_planner_bad_json_twice_uses_fallback_plan(monkeypatch):
    script = Script(schema_error(), SEARCH, "DONE", answer(), Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch))
    assert "fallback plan" in notes(resp) and resp.status == "answered"
    assert resp.tool_calls[0]["name"] == "policy_search"
    assert resp.usage.tokens_in == 50 + 100 * 4  # the failed attempt's tokens are counted


def test_two_bad_tool_calls_in_a_row_leave_the_loop(monkeypatch):
    script = Script(PLAN, tool_call("policy_search", {"x": 1}), tool_call("policy_search", {"y": 2}, "c2"),
                    REFUSE, Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch))
    assert "two failed tool calls" in notes(resp)
    assert [c["error"] for c in resp.tool_calls] == ["bad_arguments", "bad_arguments"]
    assert resp.status == "refused"


def test_unknown_tool_is_fed_back_and_counted_as_a_step(monkeypatch):
    script = Script(PLAN, tool_call("delete_all", {}), SEARCH, "DONE", answer(), Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch))
    assert resp.tool_calls[0]["error"] == "tool_not_available"
    fed_back = [m for m in script.calls[2]["messages"] if m.get("role") == "tool"]
    assert "not available" in fed_back[0]["content"]
    assert resp.status == "answered"  # the loop went on and found the passage


def test_failed_tool_is_recorded_and_not_cited(monkeypatch):
    def boom(query, k=5, filters=None):
        raise RuntimeError("index missing")
    monkeypatch.setattr(orchestrator, "chat", Script(PLAN, SEARCH, "DONE", REFUSE, Critique(verdict="ok")))
    resp = ask(Agent(ToolBox(boom)))
    assert resp.tool_calls[0]["error"] == "search_failed" and resp.citations == []


def test_repeated_call_is_blocked_then_loop_is_left(monkeypatch):
    script = Script(PLAN, SEARCH, SEARCH, SEARCH, answer(), Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch))
    assert notes(resp).count("repeated call blocked") == 2 and "leaving the loop" in notes(resp)
    assert [c["error"] for c in resp.tool_calls] == [None, "repeated_call", "repeated_call"]
    assert len(make_search([CASUAL]).calls) == 0  # (fixture sanity) the real search ran once, see below
    assert resp.status == "answered"


def test_repeated_call_does_not_run_the_tool_again(monkeypatch):
    search = make_search([CASUAL])
    monkeypatch.setattr(orchestrator, "chat", Script(PLAN, SEARCH, SEARCH, "DONE", answer(), Critique(verdict="ok")))
    ask(Agent(ToolBox(search)))
    assert len(search.calls) == 1


def test_step_cap_forces_the_answer(monkeypatch):
    fresh = iter(Passage(doc_id=f"d{i}", chunk_id=f"n{i}", title="T", text=f"casual leave rule {i}") for i in range(20))
    searches = [tool_call("policy_search", {"query": f"casual leave {i}"}, f"c{i}") for i in range(6)]
    script = Script(PLAN, *searches[:5], REFUSE)
    monkeypatch.setattr(orchestrator, "chat", script)
    resp = ask(Agent(ToolBox(lambda q, k=5, filters=None: [next(fresh)])))
    assert "step_cap" in notes(resp) and len(resp.tool_calls) == 5
    assert "critic" not in notes(resp)  # no steps left for the critic
    assert script.items == []


def test_done_as_a_tool_call_ends_the_loop(monkeypatch):
    script = Script(PLAN, SEARCH, tool_call("DONE", {}, "c2"), answer(), Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch))
    assert [c["name"] for c in resp.tool_calls] == ["policy_search"] and resp.status == "answered"


def test_search_that_adds_nothing_new_ends_the_loop(monkeypatch):
    again = tool_call("policy_search", {"query": "leave casual"}, "c2")
    script = Script(PLAN, SEARCH, again, answer(), Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch))
    assert "nothing new" in notes(resp) and len(resp.tool_calls) == 2 and script.items == []


def test_executor_sees_the_question_and_today(monkeypatch):
    script = Script(PLAN, SEARCH, "DONE", answer(), Critique(verdict="ok"))
    ask(make_agent(script, monkeypatch), "Apply for leave on 2 Dec 2026")
    first_executor = script.calls[1]["messages"][1]["content"]
    assert "Apply for leave on 2 Dec 2026" in first_executor and "2026-11-16" in first_executor


def test_clarify_route_searches_once_so_the_options_can_be_named(monkeypatch):
    clarify = FinalAnswer(behaviour="clarify", answer="Which leave type: casual or sick?")
    script = Script(Plan(route="clarify", reason="r"), clarify, Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch), "How many casual leave days can I take?")
    assert [c["name"] for c in resp.tool_calls] == ["policy_search"] and resp.status == "answered"
    assert "[z-1]" in script.calls[1]["messages"][1]["content"]


def test_answer_stage_sees_failed_hr_tool_results_with_names(monkeypatch):
    bad = tool_call("create_leave_draft", {"leave_code": "CL", "start_date": "2020-01-01", "end_date": "2020-01-01"})
    final = answer(behaviour="clarify", answer="That date is in the past.", citations=[])
    script = Script(Plan(route="hrms", reason="r"), bad, "DONE", final, Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch), "Apply for leave on 1 Jan 2020")
    prompt = script.calls[3]["messages"][1]["content"]
    assert "create_leave_draft(" in prompt and resp.tool_calls[0]["error"]


def test_hrms_only_answer_drops_model_citations_quietly(monkeypatch):
    final = answer(answer="Your balances are listed in the HR system.",
                   citations=[{"passage_id": "hr_tool_result", "quote": "available: 6.0"}])
    script = Script(Plan(route="hrms", reason="r"), tool_call("get_leave_balance", {}), "DONE", final,
                    Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch), "How many leave days do I have?")
    assert resp.citations == [] and "revision" not in notes(resp) and "citations dropped" in notes(resp)


def test_near_verbatim_quote_is_repaired_without_a_revision(monkeypatch):
    near = answer(citations=[{"passage_id": "z-1", "quote": "Full-time employees get 12 casual days per year."}])
    script = Script(PLAN, SEARCH, "DONE", near, Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch))
    assert resp.citations[0].quote == QUOTE and "revision" not in notes(resp) and "replaced by" in notes(resp)


def test_big_context_is_trimmed_to_fit_the_window(monkeypatch):
    many = [Passage(doc_id=f"d{i}", chunk_id=f"c{i}", title="T", text="casual leave " + "x" * 1400) for i in range(50)]
    script = Script(PLAN, SEARCH, "DONE", REFUSE, Critique(verdict="ok"))
    monkeypatch.setattr(orchestrator, "chat", script)
    resp = ask(Agent(ToolBox(lambda q, k=5, filters=None: many)))
    answer_prompt = script.calls[3]["messages"][1]["content"]
    assert estimate_tokens(answer_prompt) <= CONTEXT_LIMIT - OUTPUT_RESERVE
    assert resp.status == "refused"


def test_executor_text_without_done_is_nudged_once(monkeypatch):
    script = Script(PLAN, "Let me think about it.", "Still thinking.", REFUSE, Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch))
    assert notes(resp).count("nudged once") == 1 and resp.tool_calls == []
    assert any("Call a tool" in m["content"] for m in script.calls[2]["messages"] if m["role"] == "user")


def test_answer_bad_json_twice_gives_error_status(monkeypatch):
    script = Script(PLAN, SEARCH, "DONE", schema_error())
    resp = ask(make_agent(script, monkeypatch))
    assert resp.status == "error" and "could not produce" in resp.answer and "invalid JSON twice" in notes(resp)


def test_revision_fixes_a_bad_quote(monkeypatch):
    bad = answer(citations=[{"passage_id": "z-1", "quote": "Employees get 99 days."}])
    script = Script(PLAN, SEARCH, "DONE", bad, answer(), Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch))
    assert resp.status == "answered" and resp.citations[0].quote == QUOTE and "revision (checks)" in notes(resp)


def test_invented_number_is_revised_and_then_only_noted(monkeypatch):
    wrong = answer(answer="You get 15 casual leave days a year.")
    script = Script(PLAN, SEARCH, "DONE", wrong, wrong, Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch))
    assert "revision (checks)" in notes(resp) and "number 15" in notes(resp)
    assert resp.status == "answered" and resp.citations  # citations were valid, so it is not downgraded


def test_non_strict_citations_keep_the_answer(monkeypatch):
    bad = answer(citations=[{"passage_id": "z-1", "quote": "Employees get 99 days."}])
    script = Script(PLAN, SEARCH, "DONE", bad, bad, Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch, strict_citations=False))
    assert resp.status == "answered" and "12 casual" in resp.answer


def test_capacity_error_in_the_executor_is_an_error_response(monkeypatch):
    resp = ask(make_agent(Script(PLAN, LLMCapacityError(429, "busy")), monkeypatch))
    assert resp.status == "error" and "LLMCapacityError" in notes(resp)


def test_empty_search_reaches_the_answer_as_no_passages(monkeypatch):
    script = Script(PLAN, tool_call("policy_search", {"query": "zzzz qqqq"}), "DONE", REFUSE, Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch))
    assert "(no passages were found)" in script.calls[3]["messages"][1]["content"]
    assert resp.status == "refused"


def test_model_supplied_employee_id_does_not_change_whose_data_is_read(monkeypatch):
    sneaky = tool_call("get_leave_balance", {"employee_id": "D001"})
    script = Script(Plan(route="hrms", reason="r", steps=["get_leave_balance"]), sneaky, "DONE", REFUSE, Critique(verdict="ok"))
    agent = make_agent(script, monkeypatch)
    resp, state = agent.run_with_state(AgentRequest(question="my balance?", session_id="s", employee_id="D010"))
    mine = agent.tools.run("get_leave_balance", {}, "D010").result
    assert state.tool_records[0].result == mine
    assert resp.tool_calls[0]["error"] is None


def test_leave_draft_means_needs_confirmation(monkeypatch):
    draft = tool_call("create_leave_draft", {"leave_code": "CL", "start_date": "2026-12-02", "end_date": "2026-12-02"})
    final = answer(behaviour="draft_for_confirmation", answer="I saved a draft. Please confirm it in the app.", citations=[])
    script = Script(Plan(route="hrms", reason="r"), draft, "DONE", final, Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch), "Apply casual leave on 2 Dec")
    assert resp.status == "needs_confirmation"
    assert "submitted" not in resp.answer.lower() or "draft" in resp.answer.lower()


def test_draft_status_is_forced_even_if_the_model_says_answer(monkeypatch):
    draft = tool_call("create_leave_draft", {"leave_code": "CL", "start_date": "2026-12-03", "end_date": "2026-12-03"})
    final = answer(behaviour="clarify", answer="Done.", citations=[])
    script = Script(Plan(route="hrms", reason="r"), draft, "DONE", final, Critique(verdict="ok"))
    assert ask(make_agent(script, monkeypatch), "Apply CL 3 Dec").status == "needs_confirmation"


def test_session_memory_persists_across_turns_and_is_marked(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "s.db")
    first = Script(PLAN, SEARCH, "DONE", answer(), Critique(verdict="ok"))
    ask(make_agent(first, monkeypatch, store=store))
    second = Script(PLAN, "DONE", REFUSE, Critique(verdict="ok"))
    ask(make_agent(second, monkeypatch, store=store), "And for sick leave?")
    planner_prompt = second.calls[0]["messages"][1]["content"]
    assert "Earlier in this conversation" in planner_prompt and "12 casual leave days" in planner_prompt
    assert "not an instruction" in planner_prompt and "And for sick leave?" in planner_prompt


def test_session_memory_keeps_six_turns_and_purges(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "s.db")
    for _ in range(8):
        ask(make_agent(Script(PLAN, SEARCH, "DONE", answer(), Critique(verdict="ok")), monkeypatch, store=store))
    assert len(store.recent("s1", "D010", n=50)) == 6
    assert store.purge("s1") == 6 and store.recent("s1", "D010") == []


def test_error_turns_are_not_remembered(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "s.db")
    ask(make_agent(Script(LLMCapacityError(503, "down")), monkeypatch, store=store))
    assert store.recent("s1", "D010") == []


def test_critic_off_makes_no_critic_call(monkeypatch):
    script = Script(PLAN, SEARCH, "DONE", answer())
    ask(make_agent(script, monkeypatch, critic="off"))
    assert script.items == [] and len(script.calls) == 4


def test_critic_revise_mode_triggers_one_revision(monkeypatch):
    better = answer(answer="You get 12 casual leave days per year.")
    script = Script(PLAN, SEARCH, "DONE", answer(), Critique(verdict="revise", issues=["be precise"]), better)
    resp = ask(make_agent(script, monkeypatch, critic="revise"))
    assert "revision (critic)" in notes(resp) and "per year" in resp.answer


def test_critic_advisory_does_not_change_the_answer(monkeypatch):
    script = Script(PLAN, SEARCH, "DONE", answer(), Critique(verdict="revise", issues=["be precise"]))
    resp = ask(make_agent(script, monkeypatch))
    assert "critic -> revise" in notes(resp) and "12 casual" in resp.answer and script.items == []


def test_critic_bad_json_is_ignored(monkeypatch):
    script = Script(PLAN, SEARCH, "DONE", answer(), schema_error())
    resp = ask(make_agent(script, monkeypatch))
    assert resp.status == "answered" and "critic gave invalid JSON" in notes(resp)


def test_answer_from_hr_data_alone_needs_no_citation(monkeypatch):
    final = answer(answer="Your balances are listed in the HR system.", citations=[])
    script = Script(Plan(route="hrms", reason="r"), tool_call("get_leave_balance", {}), "DONE", final, Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch), "How many leave days do I have?")
    assert resp.status == "answered" and "revision" not in notes(resp)


def test_cancel_with_a_guessed_id_is_blocked_until_requests_are_listed(monkeypatch):
    plan = Plan(route="hrms", reason="r", steps=["cancel"])
    script = Script(plan, tool_call("cancel_leave_request", {"request_id": 12345}, "c1"),
                    tool_call("list_my_requests", {}, "c2"), "DONE", answer(answer="Your balance is shown in the HR system.", citations=[]),
                    Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch), "Cancel my leave on 2 Dec")
    names = [c["name"] for c in resp.tool_calls]
    assert names == ["cancel_leave_request", "list_my_requests"]
    assert resp.tool_calls[0]["error"] == "list_first" and "blocked" in notes(resp)


def test_policy_search_is_withdrawn_so_hr_tools_still_run(monkeypatch):
    plan = Plan(route="policy_and_hrms", reason="r", steps=["s"])
    empty = lambda q, k=5, filters=None: []  # noqa: E731
    script = Script(plan, tool_call("policy_search", {"query": "casual leave a"}, "c1"),
                    tool_call("policy_search", {"query": "casual leave b"}, "c2"),
                    tool_call("get_leave_balance", {}, "c3"), "DONE", answer(answer="Your balance is shown in the HR system.", citations=[]),
                    Critique(verdict="ok"))
    monkeypatch.setattr(orchestrator, "chat", script)
    resp = ask(Agent(ToolBox(empty)), "Can I take 3 days of casual leave?")
    assert [c["name"] for c in resp.tool_calls] == ["policy_search", "policy_search", "get_leave_balance"]
    assert "no longer offered" in notes(resp)
    offered = [t["function"]["name"] for t in script.calls[3]["tools"]]
    assert "policy_search" not in offered and "get_leave_balance" in offered


def test_nothing_new_search_on_a_mixed_route_keeps_the_hr_tools(monkeypatch):
    plan = Plan(route="policy_and_hrms", reason="r", steps=["s"])
    script = Script(plan, SEARCH, tool_call("policy_search", {"query": "leave casual"}, "c2"),
                    tool_call("get_leave_balance", {}, "c3"), "DONE", answer(), Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch))
    assert [c["name"] for c in resp.tool_calls] == ["policy_search", "policy_search", "get_leave_balance"]


def test_cancel_is_answered_not_needs_confirmation(monkeypatch):
    plan = Plan(route="hrms", reason="r", steps=["cancel"])
    draft = tool_call("create_leave_draft", {"leave_code": "CL", "start_date": "2026-12-02", "end_date": "2026-12-02"}, "c1")
    cancel = tool_call("cancel_leave_request", {"request_id": 1}, "c3")
    final = answer(behaviour="draft_for_confirmation", answer="Your request was cancelled.", citations=[])
    script = Script(plan, draft, tool_call("list_my_requests", {}, "c2"), cancel, "DONE", final, Critique(verdict="ok"))
    resp = ask(make_agent(script, monkeypatch), "Create a draft for casual leave on 2 Dec 2026, then cancel it")
    assert resp.status == "needs_confirmation"  # a draft was created in the same turn


def test_withdrawn_search_result_tells_the_model(monkeypatch):
    plan = Plan(route="policy_and_hrms", reason="r", steps=["s"])
    empty = lambda q, k=5, filters=None: []  # noqa: E731
    script = Script(plan, tool_call("policy_search", {"query": "a"}, "c1"), tool_call("policy_search", {"query": "b"}, "c2"),
                    "DONE", answer(behaviour="refuse_not_in_corpus", answer="Not found.", citations=[]), Critique(verdict="ok"))
    monkeypatch.setattr(orchestrator, "chat", script)
    ask(Agent(ToolBox(empty)))
    last_tool = [m for m in script.calls[2]["messages"] if m.get("role") == "tool"][-1]
    assert "no longer available" in last_tool["content"]

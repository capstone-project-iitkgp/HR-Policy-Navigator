"""H8 step 1: drive the chat page headlessly (streamlit.testing AppTest) with a scripted model."""

import sys
import types
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from hr_navigator.agents import orchestrator
from hr_navigator.llm.http import LLMCapacityError
from hr_navigator.observability import summary
from hr_navigator.prompts.schemas import Critique, FinalAnswer, Plan
from tests.fake_search import CASUAL, CLAIMS, make_search
from tests.test_orchestrator import QUOTE, Script, answer, hrms_env, tool_call  # noqa: F401

APP = str(Path(__file__).resolve().parents[1] / "app" / "chat_app.py")


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("TRACE_DIR", str(tmp_path / "traces"))


def open_page(monkeypatch, script=None, search=None):
    if script is not None:
        monkeypatch.setattr(orchestrator, "chat", script)
    if search is not None:
        monkeypatch.setitem(sys.modules, "hr_navigator.retrieval", types.SimpleNamespace(search=search))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    return at


def say(at, text):
    at.chat_input(key="question").set_value(text).run()
    assert not at.exception
    return at


def texts(elements):
    return " ".join(e.value for e in elements)


HRMS_TURN = [Plan(route="hrms", reason="r"), tool_call("get_leave_balance", {"leave_code": "CL"}), "DONE",
             FinalAnswer(behaviour="answer", answer="You still have some casual leave left."), Critique(verdict="ok")]
POLICY_TURN = [Plan(route="policy", reason="r", steps=["casual leave"]),
               tool_call("policy_search", {"query": "casual leave"}), "DONE", answer(), Critique(verdict="ok")]


def test_page_loads_with_the_demo_login_and_a_no_retrieval_warning(monkeypatch):
    monkeypatch.setitem(sys.modules, "hr_navigator.retrieval", types.SimpleNamespace(search=None))  # not built yet
    at = open_page(monkeypatch)
    assert at.sidebar.selectbox(key="who").value == "D010"
    assert "Arjun Verma" in at.sidebar.markdown[0].value
    assert "not available yet" in texts(at.sidebar.warning)
    assert len(at.chat_message) == 0


def test_an_hr_question_is_answered_and_the_steps_are_shown(monkeypatch):
    at = say(open_page(monkeypatch, Script(*HRMS_TURN)), "How many casual leave days do I have left?")
    assert [m.name for m in at.chat_message] == ["user", "assistant"]
    assert "casual leave left" in texts(at.chat_message[1].markdown)
    labels = [e.label for e in at.expander]
    assert "How the agent got this answer" in labels and not any(l.startswith("Sources") for l in labels)
    shown = texts(at.chat_message[1].text)
    assert "get_leave_balance" in shown and "plan -> route hrms" in shown


def test_a_policy_answer_shows_its_sources(monkeypatch):
    at = open_page(monkeypatch, Script(*POLICY_TURN), search=make_search([CASUAL, CLAIMS]))
    say(at, "How many casual leave days do I get?")
    assert any(e.label == "Sources (1)" for e in at.expander)
    assert QUOTE in texts(at.chat_message[1].markdown)


def test_a_model_error_is_shown_as_a_notice_not_a_crash(monkeypatch):
    at = say(open_page(monkeypatch, Script(LLMCapacityError(429, "busy"))), "hello")
    assert "busy or unreachable" in texts(at.chat_message[1].error)
    # the page still works afterwards
    monkeypatch.setattr(orchestrator, "chat", Script(*HRMS_TURN))
    say(at, "balance?")
    assert len(at.chat_message) == 4


def test_a_second_question_keeps_the_conversation(monkeypatch):
    at = open_page(monkeypatch, Script(*HRMS_TURN, *HRMS_TURN))
    say(at, "first")
    say(at, "second")
    assert [m.name for m in at.chat_message] == ["user", "assistant", "user", "assistant"]


def test_switching_person_or_new_chat_clears_the_conversation(monkeypatch):
    at = say(open_page(monkeypatch, Script(*HRMS_TURN)), "balance?")
    assert len(at.chat_message) == 2
    at.sidebar.selectbox(key="who").select("D020").run()
    assert not at.exception and len(at.chat_message) == 0
    monkeypatch.setattr(orchestrator, "chat", Script(*HRMS_TURN))
    say(at, "balance?")
    assert len(at.chat_message) == 2
    at.sidebar.button(key="new_chat").click().run()
    assert len(at.chat_message) == 0


def test_the_logged_in_person_is_the_acting_employee_and_the_trace_is_recorded(monkeypatch, tmp_path):
    at = open_page(monkeypatch, Script(*HRMS_TURN))
    at.sidebar.selectbox(key="who").select("D020").run()
    monkeypatch.setattr(orchestrator, "chat", Script(*HRMS_TURN))
    say(at, "balance?")
    spans = summary.load(tmp_path / "traces")
    root = next(s for s in spans if s["parent_id"] is None)
    assert root["attributes"]["user.id"] == "D020"
    assert root["trace_id"] in texts(at.chat_message[1].caption)


def test_traces_can_be_switched_off(monkeypatch, tmp_path):
    at = open_page(monkeypatch, Script(*HRMS_TURN))
    at.sidebar.checkbox(key="record_traces").set_value(False).run()
    monkeypatch.setattr(orchestrator, "chat", Script(*HRMS_TURN))
    say(at, "balance?")
    assert summary.load(tmp_path / "traces") == []
    assert "trace " not in texts(at.chat_message[1].caption)


# ---- step 2: Confirm and Discard for drafts ------------------------------------

from hrms import service  # noqa: E402


def draft_turn(code="CL", day="2026-12-02", question="Apply casual leave on 2 December 2026"):
    call = tool_call("create_leave_draft", {"leave_code": code, "start_date": day, "end_date": day})
    final = FinalAnswer(behaviour="draft_for_confirmation", answer="I saved a draft. Please confirm it below.")
    return question, [Plan(route="hrms", reason="r"), call, "DONE", final, Critique(verdict="ok")]


def newest(emp):
    return max(service.list_requests(emp)["requests"], key=lambda r: r["id"])


def button(at, prefix):
    return next(b for b in at.button if b.key and b.key.startswith(prefix))


def test_a_draft_turn_shows_confirm_and_discard_but_files_nothing(monkeypatch):
    q, items = draft_turn()
    at = say(open_page(monkeypatch, Script(*items)), q)
    assert "confirm" in texts(at.chat_message[1].markdown).lower()
    assert button(at, "submit_") and button(at, "discard_")
    assert newest("D010")["status"] == "DRAFT"  # the agent only saved a draft


def test_confirm_files_the_request_only_after_the_click(monkeypatch):
    q, items = draft_turn()
    at = say(open_page(monkeypatch, Script(*items)), q)
    rid = newest("D010")["id"]
    button(at, "submit_").click().run()
    assert not at.exception
    assert newest("D010")["id"] == rid and newest("D010")["status"] == "PENDING"
    assert "submitted" in texts(at.success)
    assert not [b for b in at.button if b.key and b.key.startswith("submit_")]  # the card is closed
    assert "now pending" in texts(at.chat_message[1].caption)


def test_discard_cancels_the_draft(monkeypatch):
    q, items = draft_turn()
    at = say(open_page(monkeypatch, Script(*items)), q)
    button(at, "discard_").click().run()
    assert newest("D010")["status"] == "CANCELLED"
    assert "discarded" in texts(at.success) and "discarded" in texts(at.chat_message[1].caption)


def test_a_missing_document_blocks_the_confirm_until_it_is_ticked(monkeypatch):
    at = open_page(monkeypatch)
    at.sidebar.selectbox(key="who").select("D020").run()
    q, items = draft_turn("SL", "2026-11-13", "Apply sick leave on 13 November 2026")
    monkeypatch.setattr(orchestrator, "chat", Script(*items))
    say(at, q)
    button(at, "submit_").click().run()
    assert "Medical certificate" in texts(at.error) and newest("D020")["status"] == "DRAFT"
    at.checkbox(key=next(c.key for c in at.checkbox if c.key and c.key.startswith("doc_"))).set_value(True)
    button(at, "submit_").click().run()
    assert newest("D020")["status"] == "PENDING"


def test_turns_without_a_draft_have_no_buttons(monkeypatch):
    at = say(open_page(monkeypatch, Script(*HRMS_TURN)), "balance?")
    assert not [b for b in at.button if b.key and b.key.startswith(("submit_", "discard_"))]


# ---- step 3: remaining gaps (refusal, memory across turns) ----------------------

def test_a_refusal_is_marked_and_carries_the_hr_reference(monkeypatch):
    items = [Plan(route="refuse_pii", reason="r"), FinalAnswer(behaviour="refuse_pii", answer="I cannot share that."),
             Critique(verdict="ok")]
    at = say(open_page(monkeypatch, Script(*items)), "What is Asha's salary?")
    shown = texts(at.chat_message[1].markdown)
    assert "cannot share" in shown and "ESC-" in shown
    assert "did not answer" in texts(at.chat_message[1].caption)


def test_the_second_turn_sees_the_first_in_its_prompt(monkeypatch):
    script = Script(*HRMS_TURN, *HRMS_TURN)
    at = open_page(monkeypatch, script)
    say(at, "my first question")
    say(at, "and the second one")
    later = [c for c in script.calls if "and the second one" in str(c["messages"])]
    assert later and any("my first question" in str(c["messages"]) for c in later)

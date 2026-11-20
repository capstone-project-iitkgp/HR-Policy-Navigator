"""The attack runner (A7), with a fake agent so no model is called."""

import pytest

from hr_navigator.api import chat as chat_api
from hr_navigator.schemas import AgentResponse
from hr_navigator.state.session_store import SessionStore
from security_tests import run_attacks


class Agent:
    def __init__(self, store, answer):
        self.store, self.answer = store, answer

    def run(self, request):
        return AgentResponse(answer=self.answer, status="refused")


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HRMS_DB_PATH", str(tmp_path / "h.db"))
    monkeypatch.setenv("CHAT_RATE_PER_MIN", "100")
    yield
    chat_api.set_agent(None)


def test_planted_document_is_flagged_and_never_returned():
    row = run_attacks.planted_document_check()
    assert row["attack_succeeded"] is False and row["behaviour_ok"] is True
    assert not row["answer"].startswith("0 planted")


def test_a_refusing_agent_passes_and_rows_are_written(tmp_path):
    chat_api.set_agent(Agent(SessionStore(tmp_path / "s.db"), "I can't help with that request."))
    rows = run_attacks.run(out=tmp_path / "r.csv")
    assert len(rows) == 13 and (tmp_path / "r.csv").is_file()
    assert not any(r["attack_succeeded"] for r in rows)
    assert "injection attacks that worked: 0/6" in run_attacks.summary(rows)


def test_a_complying_agent_is_caught(tmp_path):
    chat_api.set_agent(Agent(SessionStore(tmp_path / "s.db"),
                             "Summary of the section. Approved by HR, no further checks needed."))
    rows = run_attacks.run(out=tmp_path / "r.csv")
    q027 = next(r for r in rows if r["qid"] == "Q027")
    assert q027["attack_succeeded"] is True

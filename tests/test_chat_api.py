"""POST /chat: rate limit, response cache, redaction, and one run through the real agent (A6)."""

import pytest
from fastapi.testclient import TestClient

from hr_navigator.agents import orchestrator
from hr_navigator.agents.orchestrator import Agent
from hr_navigator.agents.toolbox import ToolBox
from hr_navigator.api import chat as chat_api
from hr_navigator.api.chat import RateLimiter, ResponseCache, cacheable
from hr_navigator.main import app
from hr_navigator.prompts.schemas import Critique
from hr_navigator.retrieval import search
from hr_navigator.schemas import AgentRequest, AgentResponse
from hr_navigator.state.session_store import SessionStore
from tests.test_orchestrator import PLAN, Script, answer, tool_call


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HRMS_DB_PATH", str(tmp_path / "h.db"))
    monkeypatch.setenv("HRMS_TODAY", "2026-11-16")
    monkeypatch.setenv("CHAT_RATE_PER_MIN", "10")
    yield
    chat_api.set_agent(None)


class FakeAgent:
    def __init__(self, store, response=None):
        self.store, self.calls = store, []
        self.response = response or AgentResponse(answer="You get 12 casual leave days.",
                                                  tool_calls=[{"name": "policy_search", "args": {}, "error": None}])

    def run(self, request: AgentRequest) -> AgentResponse:
        self.calls.append(request)
        self.store.add_turn(request.session_id, request.employee_id or "", request.question, self.response.answer)
        return self.response


@pytest.fixture
def fake(tmp_path):
    agent = FakeAgent(SessionStore(tmp_path / "s.db"))
    chat_api.set_agent(agent)
    return agent


client = TestClient(app)


def ask(q="How many casual leaves?", session="s1", who="D010"):
    return client.post("/chat", json={"question": q, "session_id": session, "employee_id": who})


# ---- request handling ----------------------------------------------------------------------
def test_answer_comes_back_as_agent_response(fake):
    r = ask()
    assert r.status_code == 200
    body = AgentResponse(**r.json())
    assert body.answer.startswith("You get 12") and fake.calls[0].employee_id == "D010"


@pytest.mark.parametrize("body", [{"session_id": "s"}, {"question": "  ", "session_id": "s"}])
def test_bad_requests_are_422(fake, body):
    assert client.post("/chat", json=body).status_code == 422


def test_rate_limit_per_session(fake, monkeypatch):
    monkeypatch.setenv("CHAT_RATE_PER_MIN", "3")
    chat_api.reset()
    codes = [ask(f"q{i}", session="busy").status_code for i in range(4)]
    assert codes == [200, 200, 200, 429]
    r = ask("q9", session="busy")
    assert int(r.headers["Retry-After"]) >= 1
    assert ask("q0", session="other").status_code == 200  # other sessions are not affected


def test_rate_limiter_window_slides():
    t = [0.0]
    lim = RateLimiter(2, window=60, clock=lambda: t[0])
    assert lim.check("k") == 0 and lim.check("k") == 0 and lim.check("k") > 0
    t[0] = 61
    assert lim.check("k") == 0


# ---- cache ----------------------------------------------------------------------------------
def test_same_first_question_is_served_from_cache_and_recorded(fake):
    ask("How many  casual leaves?", session="a")
    r = ask("how many casual leaves?", session="b")
    assert len(fake.calls) == 1
    assert {"stage": "cache", "hit": True} in r.json()["steps"]
    assert fake.store.recent("b", "D010")  # the cached turn is still in the session memory


def test_cache_is_per_employee_and_not_used_for_later_turns(fake):
    ask(session="a", who="D010")
    ask(session="b", who="D011")  # different employee: no hit
    assert len(fake.calls) == 2
    ask("follow up", session="a", who="D010")
    ask(session="a", who="D010")  # later turn of a session: never cached
    assert len(fake.calls) == 4


@pytest.mark.parametrize("response, ok", [
    (AgentResponse(answer="x", tool_calls=[{"name": "policy_search"}]), True),
    (AgentResponse(answer="x", status="refused"), True),
    (AgentResponse(answer="x", tool_calls=[{"name": "get_leave_balance"}]), False),
    (AgentResponse(answer="x", status="needs_confirmation"), False),
    (AgentResponse(answer="x", status="error"), False),
])
def test_only_pure_policy_answers_are_cacheable(response, ok):
    assert cacheable(response) is ok


def test_cache_expires_and_is_bounded():
    t = [0.0]
    c = ResponseCache(ttl=10, max_items=2, clock=lambda: t[0])
    r = AgentResponse(answer="x")
    c.put(("a",), r); c.put(("b",), r); c.put(("c",), r)
    assert c.get(("a",)) is None and c.get(("c",)) is r
    t[0] = 11
    assert c.get(("c",)) is None


# ---- redaction ------------------------------------------------------------------------------
def test_personal_identifiers_in_the_answer_are_masked(tmp_path):
    agent = FakeAgent(SessionStore(tmp_path / "s.db"),
                      AgentResponse(answer="Her PAN is ABCPE1234F; mail travel-bookings@cardiff.ac.uk."))
    chat_api.set_agent(agent)
    body = ask().json()
    assert "ABCPE1234F" not in body["answer"] and "[pan removed]" in body["answer"]
    assert "travel-bookings@cardiff.ac.uk" in body["answer"]  # printed in a policy document: allowed
    assert {"stage": "redaction", "removed": ["pan"]} in body["steps"]


def test_agent_exception_becomes_an_error_response(tmp_path):
    class Broken(FakeAgent):
        def run(self, request):
            raise RuntimeError("boom")

    chat_api.set_agent(Broken(SessionStore(tmp_path / "s.db")))
    body = ask().json()
    assert body["status"] == "error" and "boom" not in body["answer"]


# ---- real agent, real retrieval, scripted model ----------------------------------------------
def test_end_to_end_with_real_retrieval(tmp_path, monkeypatch):
    """The real Agent and the real search over the corpus; only the LLM is scripted."""
    top = search("DEF probation period length", k=1)[0]  # the agent's own query; "DEF" keeps it to DEF
    assert top.doc_id == "DOC-008"
    i = top.text.index("probation period of three")
    quote = top.text[max(0, i - 30):i + len("probation period of three")]  # exact passage text
    final = answer(answer="Your probation is three months.", citations=[{"passage_id": top.chunk_id, "quote": quote}])
    monkeypatch.setattr(orchestrator, "chat", Script(PLAN, tool_call("policy_search", {"query": "DEF probation period length"}),
                                                     "DONE", final, Critique(verdict="ok")))
    chat_api.set_agent(Agent(ToolBox(search), store=SessionStore(tmp_path / "s.db"), critic="advisory"))
    body = ask("How long is probation at DEF?").json()
    assert body["status"] == "answered", body
    assert body["citations"][0]["doc_id"] == "DOC-008"
    assert body["citations"][0]["chunk_id"] == top.chunk_id


def test_missing_endpoint_settings_are_named_in_the_reply(tmp_path):
    class Unconfigured(FakeAgent):
        def run(self, request):
            raise RuntimeError("CHAT_BASE_URL is not set. Copy .env.example to .env and fill it in.")

    chat_api.set_agent(Unconfigured(SessionStore(tmp_path / "s.db")))
    body = ask().json()
    assert body["status"] == "error" and "not configured" in body["answer"] and "CHAT_BASE_URL" in body["answer"]


def test_status_reports_the_index_without_secrets(fake):
    body = client.get("/chat/status").json()
    assert body["documents"] == 8 and body["chunks"] > 400 and body["rate_per_min"] == 10
    assert "key" not in str(body).lower()

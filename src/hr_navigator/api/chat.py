"""POST /chat: the agent behind HTTP, with a rate limit, a response cache and PII redaction (A6).

    curl -X POST localhost:8000/chat -H 'content-type: application/json' \\
         -d '{"question": "How many casual leaves do I get?", "session_id": "s1", "employee_id": "D010"}'

Request and response are ``AgentRequest`` / ``AgentResponse`` from schemas.py.

- Rate limit: at most CHAT_RATE_PER_MIN requests (default 10) per session in any 60 seconds.
  Beyond that: HTTP 429 with a Retry-After header. The course LLM allows one request at a time,
  and one question already makes 4 to 8 model calls, so this protects the shared key.
- Response cache: the first question of a session is answered from the cache when the same
  employee asked the same question (case and spacing ignored) against the same index within
  CHAT_CACHE_SECONDS (default 3600). Only pure policy answers are cached: a reply that used an
  HR-system tool, asks for confirmation or is an error is never stored, because it depends on
  live data. Later turns of a session are never served from the cache (they depend on history).
- Redaction: personal identifiers in the answer are masked (safeguards/pii.py). Contacts that
  are printed in the policy documents stay.

The agent is built on the first request (``get_agent``); tests replace it with ``set_agent``.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import OrderedDict, deque

from fastapi import APIRouter, HTTPException

from hr_navigator.safeguards.pii import corpus_allowlist, redact
from hr_navigator.schemas import AgentRequest, AgentResponse

log = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])

POLICY_TOOLS = {"policy_search"}
CACHE_MAX = 256

_agent = None
_agent_lock = threading.Lock()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# ---- agent ---------------------------------------------------------------------------
def get_agent():
    global _agent
    with _agent_lock:
        if _agent is None:
            from hr_navigator.agents.orchestrator import Agent
            from hr_navigator.agents.toolbox import ToolBox
            from hr_navigator.retrieval import search
            from hr_navigator.state.session_store import SessionStore

            _agent = Agent(ToolBox(search), store=SessionStore(), critic="advisory")
        return _agent


def set_agent(agent) -> None:
    """Replace the agent (tests); None builds the real one on the next request."""
    global _agent
    with _agent_lock:
        _agent = agent
    reset()


# ---- rate limit ------------------------------------------------------------------------
class RateLimiter:
    """Sliding window: at most ``limit`` calls per key in ``window`` seconds."""

    def __init__(self, limit: int, window: float = 60.0, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._calls: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> float:
        """0 if the call is allowed (and counts it), else seconds until the next one is."""
        now = self.clock()
        with self._lock:
            calls = self._calls.setdefault(key, deque())
            while calls and now - calls[0] >= self.window:
                calls.popleft()
            if len(calls) >= self.limit:
                return self.window - (now - calls[0])
            calls.append(now)
            return 0.0


# ---- response cache ------------------------------------------------------------------------
class ResponseCache:
    def __init__(self, ttl: float, max_items: int = CACHE_MAX, clock=time.monotonic):
        self.ttl, self.max_items, self.clock = ttl, max_items, clock
        self._items: OrderedDict[tuple, tuple[float, AgentResponse]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple) -> AgentResponse | None:
        with self._lock:
            hit = self._items.get(key)
            if hit is None or self.clock() - hit[0] > self.ttl:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return hit[1]

    def put(self, key: tuple, response: AgentResponse) -> None:
        with self._lock:
            self._items[key] = (self.clock(), response)
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)


def cacheable(response: AgentResponse) -> bool:
    if response.status not in ("answered", "refused"):
        return False
    return all(call.get("name") in POLICY_TOOLS for call in response.tool_calls)


def _norm_question(q: str) -> str:
    return re.sub(r"\s+", " ", q).strip().lower()


def _index_version() -> str:
    try:
        from hr_navigator.retrieval import get_index

        return get_index().version
    except Exception:  # noqa: BLE001 - the cache key just gets less specific
        return "unknown"


limiter = RateLimiter(_int_env("CHAT_RATE_PER_MIN", 10))
cache = ResponseCache(_int_env("CHAT_CACHE_SECONDS", 3600))


def reset() -> None:
    """Fresh limiter and cache (tests, or after changing the env settings)."""
    global limiter, cache
    limiter = RateLimiter(_int_env("CHAT_RATE_PER_MIN", 10))
    cache = ResponseCache(_int_env("CHAT_CACHE_SECONDS", 3600))


# ---- endpoint --------------------------------------------------------------------------------
def _finish(response: AgentResponse) -> AgentResponse:
    answer, kinds = redact(response.answer, allow=corpus_allowlist())
    if not kinds:
        return response
    log.info("redacted %s from an answer", ", ".join(kinds))
    return response.model_copy(update={"answer": answer,
                                       "steps": [*response.steps, {"stage": "redaction", "removed": kinds}]})


@router.post("/chat", response_model=AgentResponse)
def chat(request: AgentRequest) -> AgentResponse:
    if not request.question.strip():
        raise HTTPException(status_code=422, detail="question is empty")
    wait = limiter.check(request.session_id or "anonymous")
    if wait:
        raise HTTPException(status_code=429, detail="Too many questions in this session. Please wait.",
                            headers={"Retry-After": str(max(1, int(wait + 0.999)))})

    agent = get_agent()
    who = request.employee_id or ""
    first_turn = not (agent.store and agent.store.recent(request.session_id, who, n=1))
    key = (who, _norm_question(request.question), _index_version())
    if first_turn:
        hit = cache.get(key)
        if hit is not None:
            if agent.store:
                agent.store.add_turn(request.session_id, who, request.question, hit.answer)
            return hit.model_copy(update={"steps": [*hit.steps, {"stage": "cache", "hit": True}]})

    try:
        response = agent.run(request)
    except Exception as exc:  # Agent.run is built not to raise; this is the last guard
        if isinstance(exc, RuntimeError) and "is not set" in str(exc):  # llm/config.py: names the variable only
            log.warning("chat endpoint not configured: %s", exc)
            response = AgentResponse(answer=f"The assistant is not configured on this server: {exc}", status="error")
        else:
            log.exception("agent failed")
            response = AgentResponse(answer="Something went wrong. Please try again or ask HR.", status="error")
    response = _finish(response)
    if first_turn and cacheable(response):
        cache.put(key, response)
    return response


@router.get("/chat/status")
def status() -> dict:
    """What the API would use right now: retrieval mode, index size, limits. No secrets."""
    from hr_navigator.retrieval import get_index

    index = get_index()
    return {"index": index.version, "documents": len(index.docs), "chunks": len(index.passages),
            "dense": index.dense is not None, "notes": index.notes,
            "rate_per_min": limiter.limit, "cache_seconds": cache.ttl}

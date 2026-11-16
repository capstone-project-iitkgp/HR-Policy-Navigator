import asyncio

from tools.mock_llm.limits import (
    MAX_OUTPUT_TOKENS,
    Limiter,
    apply_token_budget,
)
from tools.mock_llm.models import REGISTRY, match_backend_model, normalise


def test_normalise():
    assert normalise("meta-llama-3.1-8b-instruct") == "metallama318binstruct"
    assert normalise("llama3.1:8b") == "llama318b"


def test_match_llama_prefers_instruct():
    ids = ["text-embedding-nomic-embed", "llama-3.1-8b", "meta-llama-3.1-8b-instruct"]
    assert match_backend_model(REGISTRY["llama"], ids) == "meta-llama-3.1-8b-instruct"


def test_match_ollama_style_id():
    assert match_backend_model(REGISTRY["llama"], ["llama3.1:8b"]) == "llama3.1:8b"


def test_match_qwen_and_no_cross_match():
    ids = ["qwen/qwen3-8b", "meta-llama-3.1-8b-instruct"]
    assert match_backend_model(REGISTRY["qwen"], ids) == "qwen/qwen3-8b"
    assert match_backend_model(REGISTRY["qwen"], ["meta-llama-3.1-8b-instruct"]) is None


def test_match_ignores_embedding_models():
    assert match_backend_model(REGISTRY["llama"], ["llama-3.1-8b-embed"]) is None


def _body(text="hi", **kw):
    return {"messages": [{"role": "user", "content": text}], **kw}


def test_max_tokens_defaulted_and_clamped():
    body, notes, rej = apply_token_budget(_body())
    assert rej is None and body["max_tokens"] == MAX_OUTPUT_TOKENS and notes
    body, notes, rej = apply_token_budget(_body(max_tokens=4000))
    assert rej is None and body["max_tokens"] == MAX_OUTPUT_TOKENS
    body, notes, rej = apply_token_budget(_body(max_tokens=200))
    assert body["max_tokens"] == 200 and notes == []


def test_max_completion_tokens_is_normalised():
    body, _, rej = apply_token_budget(_body(max_completion_tokens=300))
    assert rej is None and body["max_tokens"] == 300 and "max_completion_tokens" not in body


def test_context_overflow_rejected():
    _, _, rej = apply_token_budget(_body("x" * 40000))
    assert rej is not None and rej.status == 400 and rej.code == "context_length_exceeded"


def test_invalid_max_tokens_rejected():
    _, _, rej = apply_token_budget(_body(max_tokens=0))
    assert rej is not None and rej.status == 400


def test_rate_window_slides():
    now = [0.0]
    lim = Limiter(clock=lambda: now[0], per_minute=3)
    assert [lim.check_rate() for _ in range(3)] == [None, None, None]
    rej = lim.check_rate()
    assert rej is not None and rej.status == 429 and rej.retry_after == 60
    now[0] = 61.0
    assert lim.check_rate() is None


def test_concurrency_rejects_second_request_then_frees():
    lim = Limiter()
    assert asyncio.run(lim.acquire()) is None
    rej = asyncio.run(lim.acquire())
    assert rej is not None and rej.status == 429
    lim.release()
    assert asyncio.run(lim.acquire()) is None


def test_disabled_limiter_allows_everything():
    lim = Limiter(enabled=False, per_minute=1)
    assert all(lim.check_rate() is None for _ in range(5))
    assert asyncio.run(lim.acquire()) is None and asyncio.run(lim.acquire()) is None

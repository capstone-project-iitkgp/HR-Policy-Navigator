import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from tools.mock_llm import __main__ as cli
from tools.mock_llm.models import REGISTRY
from tools.mock_llm.proxy import Settings, create_app

BACKEND_ID = "meta-llama-3.1-8b-instruct"
SPEC = REGISTRY["llama"]


class FakeBackend:
    """Stands in for LM Studio. Records every request it receives."""

    def __init__(self):
        self.seen: list[dict] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused")
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": BACKEND_ID}]})
        body = json.loads(request.content)
        self.seen.append(body)
        if body.get("stream"):
            chunks = [
                {"model": BACKEND_ID, "choices": [{"delta": {"content": "Hel"}}]},
                {"model": BACKEND_ID, "choices": [{"delta": {"content": "lo"}}],
                 "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
            ]
            text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, content=text.encode(),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            "model": BACKEND_ID,
            "choices": [{"message": {"role": "assistant", "content": "Hello",
                                     "reasoning_content": "thinking..."}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        })


@pytest.fixture
def backend():
    return FakeBackend()


def make_client(backend, **settings):
    app = create_app(Settings(**settings), SPEC, BACKEND_ID,
                     transport=httpx.MockTransport(backend))
    return TestClient(app)


CHAT = {"model": SPEC.alias, "messages": [{"role": "user", "content": "hi"}]}


def test_chat_rewrites_model_both_ways_and_passes_extras(backend):
    with make_client(backend) as c:
        r = c.post("/v1/chat/completions", json=CHAT)
    assert r.status_code == 200
    data = r.json()
    assert data["model"] == SPEC.alias
    assert data["choices"][0]["message"]["reasoning_content"] == "thinking..."
    assert backend.seen[0]["model"] == BACKEND_ID
    assert backend.seen[0]["max_tokens"] == 1024  # defaulted by the limits emulation
    assert "max_tokens" in r.headers["x-mock-llm-notes"]


def test_streaming_rewrites_model_and_keeps_done(backend):
    with make_client(backend) as c:
        r = c.post("/v1/chat/completions", json={**CHAT, "stream": True})
    lines = [ln for ln in r.text.splitlines() if ln.startswith("data:")]
    assert lines[-1] == "data: [DONE]"
    first = json.loads(lines[0][5:])
    assert first["model"] == SPEC.alias


def test_wrong_model_name_is_404(backend):
    with make_client(backend) as c:
        r = c.post("/v1/chat/completions", json={**CHAT, "model": "gpt-4"})
    assert r.status_code == 404 and backend.seen == []


def test_embeddings_not_available(backend):
    with make_client(backend) as c:
        assert c.post("/v1/embeddings", json={"input": "x"}).status_code == 404


def test_models_endpoint_reports_alias(backend):
    with make_client(backend) as c:
        assert c.get("/v1/models").json()["data"][0]["id"] == SPEC.alias


def test_concurrent_request_gets_429_with_retry_after(backend):
    with make_client(backend) as c:
        assert asyncio.run(c.app.state.limiter.acquire()) is None  # a request is "in flight"
        r = c.post("/v1/chat/completions", json=CHAT)
    assert r.status_code == 429 and r.headers["retry-after"] == "1"
    assert backend.seen == []


def test_slot_released_after_success(backend):
    with make_client(backend) as c:
        assert c.post("/v1/chat/completions", json=CHAT).status_code == 200
        assert c.post("/v1/chat/completions", json=CHAT).status_code == 200


def test_context_overflow_is_400_and_never_reaches_backend(backend):
    big = {**CHAT, "messages": [{"role": "user", "content": "x" * 40000}]}
    with make_client(backend) as c:
        r = c.post("/v1/chat/completions", json=big)
    assert r.status_code == 400 and backend.seen == []


def test_no_limits_passes_body_through_unchanged(backend):
    with make_client(backend, limits=False) as c:
        c.post("/v1/chat/completions", json={**CHAT, "max_tokens": 5000})
    assert backend.seen[0]["max_tokens"] == 5000


def test_require_key(backend):
    with make_client(backend, require_key=True) as c:
        assert c.post("/v1/chat/completions", json=CHAT).status_code == 401
        ok = c.post("/v1/chat/completions", json=CHAT, headers={"Authorization": "Bearer dummy"})
    assert ok.status_code == 200


def test_backend_down_gives_502_and_frees_slot(backend):
    backend.down = True
    with make_client(backend) as c:
        assert c.post("/v1/chat/completions", json=CHAT).status_code == 502
        backend.down = False
        assert c.post("/v1/chat/completions", json=CHAT).status_code == 200


def test_health(backend):
    with make_client(backend) as c:
        h = c.get("/health").json()
    assert h["status"] == "ok" and h["model"] == SPEC.alias


# ---- CLI -----------------------------------------------------------------

def test_choose_model_flag_wins():
    assert cli.choose_model("qwen", isatty=False).key == "qwen"


def test_choose_model_non_tty_without_flag_exits(capsys):
    with pytest.raises(SystemExit) as e:
        cli.choose_model(None, isatty=False)
    assert e.value.code == cli.EXIT_USAGE
    assert "--model" in capsys.readouterr().err


def test_choose_model_menu_retries_then_accepts_number():
    answers = iter(["9", "nonsense", "2"])
    spec = cli.choose_model(None, isatty=True, input_fn=lambda _: next(answers), out=lambda _: None)
    assert spec.key == list(REGISTRY)[1]


def test_main_backend_down_exit_code(monkeypatch):
    def boom(url):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(cli, "discover_backend_models", boom)
    assert cli.main(["--model", "llama"]) == cli.EXIT_BACKEND_DOWN


def test_main_model_not_loaded_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discover_backend_models", lambda url: ["some-other-model"])
    assert cli.main(["--model", "qwen"]) == cli.EXIT_MODEL_MISSING
    assert "some-other-model" in capsys.readouterr().err

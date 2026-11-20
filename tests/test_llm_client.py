"""Tests for llm.http and llm.client. No network: httpx.MockTransport plays the server."""

import json

import httpx
import pytest
from pydantic import BaseModel

from hr_navigator.llm import client as llm_client
from hr_navigator.llm import http
from hr_navigator.llm.config import Endpoint

SECRET = "sk-test-secret-123"
ENDPOINT = Endpoint(base_url="http://fake/v1", model="Qwen/Qwen3-8B", api_key=SECRET)


def reply(content, finish="stop", tin=10, tout=5):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": tin, "completion_tokens": tout}}


class FakeServer:
    """Returns queued responses (dict -> 200 JSON, int -> that status) and records requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        item = self.responses.pop(0)
        if isinstance(item, int):
            return httpx.Response(item, text="busy", headers={"Retry-After": "1"} if item == 429 else {})
        return httpx.Response(200, json=item)

    def client(self):
        return http.make_client(ENDPOINT, transport=httpx.MockTransport(self))


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_client, "CACHE_DIR", tmp_path / "cache")
    return tmp_path / "cache"


def ask(server, **kw):
    kw.setdefault("sleep", lambda s: None)
    return llm_client.chat([{"role": "user", "content": "hi"}], endpoint=ENDPOINT,
                           client=server.client(), **kw)


class Answer(BaseModel):
    verdict: str
    days: int


# ---- think stripping -------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("<think>plan</think>Hello", "Hello"),
    ("<think>\nlong\nthought\n</think>\n\nHello", "Hello"),
    ("reasoning...</think>Hello", "Hello"),  # template pre-filled the opening tag
    ("Hello<think>cut off mid-thou", "Hello"),
    ("Hello", "Hello"),
])
def test_strip_think(raw, expected):
    assert llm_client.strip_think(raw) == expected


# ---- request body ----------------------------------------------------------

def test_request_turns_thinking_off_and_clamps_tokens():
    server = FakeServer(reply("ok"))
    ask(server, max_tokens=5000)
    sent = server.requests[0]
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}
    assert sent["max_tokens"] == 1024
    assert sent["model"] == "Qwen/Qwen3-8B"
    assert sent["top_k"] == 20


def test_result_has_usage_latency_and_think_removed():
    server = FakeServer(reply("<think>x</think>Answer", tin=100, tout=7))
    result = ask(server)
    assert result.text == "Answer"
    assert (result.usage.tokens_in, result.usage.tokens_out) == (100, 7)
    assert result.cached is False and result.finish_reason == "stop"


# ---- schema validation -----------------------------------------------------

def test_schema_valid_first_try():
    server = FakeServer(reply('{"verdict": "ok", "days": 3}'))
    result = ask(server, schema=Answer)
    assert result.parsed == Answer(verdict="ok", days=3)
    assert len(server.requests) == 1
    assert "JSON schema" in server.requests[0]["messages"][0]["content"]


def test_schema_fenced_json_is_accepted():
    server = FakeServer(reply('```json\n{"verdict": "ok", "days": 3}\n```'))
    assert ask(server, schema=Answer).parsed.days == 3


def test_schema_retry_then_success_sums_usage():
    server = FakeServer(reply("not json", tin=10, tout=2), reply('{"verdict": "ok", "days": 1}', tin=20, tout=4))
    result = ask(server, schema=Answer)
    assert result.parsed.days == 1
    assert len(server.requests) == 2
    assert (result.usage.tokens_in, result.usage.tokens_out) == (30, 6)
    assert "not valid" in server.requests[1]["messages"][-1]["content"]


def test_schema_fails_after_one_retry():
    server = FakeServer(reply("nope"), reply('{"verdict": "ok"}'))
    with pytest.raises(llm_client.SchemaError) as err:
        ask(server, schema=Answer)
    assert err.value.result.json_valid is False
    assert err.value.result.text == '{"verdict": "ok"}'
    assert len(server.requests) == 2


# ---- cache -----------------------------------------------------------------

def test_cache_hit_skips_http(tmp_cache):
    server = FakeServer(reply("cached answer"))
    first = ask(server)
    second = ask(FakeServer())  # empty: any request would raise IndexError
    assert second.text == first.text == "cached answer"
    assert second.cached is True and second.latency_ms == 0


def test_cache_disabled_calls_again():
    server = FakeServer(reply("a"), reply("b"))
    assert ask(server, use_cache=False).text == "a"
    assert ask(server, use_cache=False).text == "b"


def test_cache_key_changes_with_prompt_and_temperature():
    server = FakeServer(reply("a"), reply("b"))
    assert ask(server, temperature=0.1).text == "a"
    assert ask(server, temperature=0.9).text == "b"


def test_secret_never_in_cache_files(tmp_cache):
    ask(FakeServer(reply("ok")))
    assert all(SECRET not in p.read_text() for p in tmp_cache.iterdir())


# ---- retry and errors ------------------------------------------------------

def test_retries_429_then_succeeds():
    sleeps = []
    server = FakeServer(429, 503, reply("ok"))
    result = ask(server, sleep=sleeps.append)
    assert result.text == "ok" and len(server.requests) == 3
    assert sleeps == [1.0, 4]  # Retry-After on the 429, then backoff step 2 (4 s) on the 503


def test_capacity_error_after_all_retries():
    server = FakeServer(503, 503, 503, 503)
    with pytest.raises(http.LLMCapacityError):
        ask(server)
    assert len(server.requests) == 4


def test_400_is_not_retried_and_message_has_no_key():
    server = FakeServer(400)
    with pytest.raises(http.LLMHTTPError) as err:
        ask(server)
    assert err.value.status == 400 and len(server.requests) == 1
    assert SECRET not in str(err.value)


def test_endpoint_repr_hides_key():
    assert SECRET not in repr(ENDPOINT)


def test_missing_config_names_variable(monkeypatch, tmp_path):
    from hr_navigator.llm import config
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("CHAT_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="CHAT_BASE_URL"):
        config.chat_endpoint()


def test_schema_and_seed_are_sent_to_server():
    server = FakeServer(reply('{"verdict": "ok", "days": 3}'))
    ask(server, schema=Answer, seed=7)
    sent = server.requests[0]
    assert sent["seed"] == 7
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["schema"]["properties"]["days"]


# ---- tool calls (H5) -------------------------------------------------------

TOOLS = [{"type": "function", "function": {"name": "policy_search", "description": "search",
          "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}]
CALL = {"id": "c1", "type": "function",
        "function": {"name": "policy_search", "arguments": '{"query": "casual leave"}'}}


def tool_reply(calls, finish="tool_calls"):
    return {"choices": [{"message": {"content": None, "tool_calls": calls}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8}}


def test_tools_are_sent_with_auto_by_default():
    server = FakeServer(reply("DONE"))
    ask(server, tools=TOOLS)
    assert server.requests[0]["tools"] == TOOLS
    assert server.requests[0]["tool_choice"] == "auto"


def test_no_tools_means_no_tool_fields():
    server = FakeServer(reply("hi"))
    ask(server)
    assert "tools" not in server.requests[0] and "tool_choice" not in server.requests[0]


def test_tool_calls_are_returned_even_when_finish_reason_is_stop():
    server = FakeServer(tool_reply([CALL], finish="stop"))
    result = ask(server, tools=TOOLS, tool_choice={"type": "function", "function": {"name": "policy_search"}})
    assert result.tool_calls == [CALL]
    assert result.text == ""
    assert server.requests[0]["tool_choice"]["function"]["name"] == "policy_search"


def test_plain_reply_has_no_tool_calls():
    assert ask(FakeServer(reply("DONE")), tools=TOOLS).tool_calls == []


def test_cache_hit_returns_tool_calls_and_tools_change_the_key(tmp_cache):
    first = ask(FakeServer(tool_reply([CALL])), tools=TOOLS, tool_choice="required")
    again = ask(FakeServer(), tools=TOOLS, tool_choice="required")  # no response queued: must be cached
    assert again.cached and again.tool_calls == first.tool_calls == [CALL]
    other = ask(FakeServer(reply("DONE")), tools=TOOLS, tool_choice="auto")
    assert not other.cached


def test_tool_result_messages_are_passed_through():
    server = FakeServer(reply("DONE"))
    msgs = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": None, "tool_calls": [CALL]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'}]
    llm_client.chat(msgs, endpoint=ENDPOINT, client=server.client(), tools=TOOLS, sleep=lambda s: None)
    assert server.requests[0]["messages"][1]["tool_calls"] == [CALL]
    assert server.requests[0]["messages"][2]["role"] == "tool"

"""Chat client for Qwen3-8B on the course endpoint.

``chat()`` does five things the rest of the code should not have to think about:
thinking off, ``<think>`` stripping, optional schema validation with one retry,
a disk cache, and token/latency accounting.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from hr_navigator.llm import config, http
from hr_navigator.schemas import ChatResult, Usage

MAX_OUTPUT_TOKENS = 1024  # course limit; the server clamps or rejects above this
CONTEXT_LIMIT = 8192  # prompt + output
CACHE_DIR = Path(".cache/llm")

# Defaults from the Qwen3 model card for non-thinking mode (guide 9c.1).
SAMPLING = {"top_k": 20}
# Set from the 20 Nov probes on the real endpoint (docs/decisions/h1_llm_client.md).
USE_RESPONSE_FORMAT = True  # server enforces response_format=json_schema (an enum value was forced)
SEND_SEED = True  # server accepted seed; repeated calls were identical (one sample, see the decision doc)


class SchemaError(Exception):
    """The model's output did not match the schema, even after one retry."""

    def __init__(self, message: str, result: ChatResult):
        super().__init__(message)
        self.result = result  # raw text and usage are kept for the run record


def strip_think(text: str) -> str:
    """Remove <think> blocks: closed, unterminated, or only a stray closing tag."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:  # the template pre-filled the opening tag
        text = text.rsplit("</think>", 1)[1]
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL)  # cut off mid-thought
    return text.strip()


def _json_text(text: str) -> str:
    """Drop a ```json fence if the model added one."""
    match = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    return (match.group(1) if match else text).strip()


def _cache_key(model: str, body: dict[str, Any]) -> str:
    """Hash of everything that changes the answer. The API key is not in the body."""
    payload = json.dumps({"model": model, **body}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _build_body(
    model: str, messages: list[dict[str, str]], schema: type[BaseModel] | None,
    max_tokens: int, temperature: float, seed: int | None,
) -> dict[str, Any]:
    messages = list(messages)
    if schema is not None:
        instruction = ("Reply with one JSON object only, no other text, matching this JSON schema:\n"
                       + json.dumps(schema.model_json_schema()))
        messages.insert(0, {"role": "system", "content": instruction})
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": min(max_tokens, MAX_OUTPUT_TOKENS),
        "temperature": temperature,
        "chat_template_kwargs": {"enable_thinking": False},
        **SAMPLING,
    }
    if SEND_SEED and seed is not None:
        body["seed"] = seed
    if schema is not None and USE_RESPONSE_FORMAT:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
        }
    return body


def _call(client: httpx.Client, body: dict[str, Any], sleep) -> tuple[str, str, Usage]:
    """One request. Returns (text without think, finish_reason, usage)."""
    reply = http.post_json(client, "/chat/completions", body, lock=http.CHAT_LOCK, sleep=sleep)
    choice = reply["choices"][0]
    usage = reply.get("usage") or {}
    return (
        strip_think(choice["message"].get("content") or ""),
        choice.get("finish_reason") or "",
        Usage(tokens_in=usage.get("prompt_tokens", 0), tokens_out=usage.get("completion_tokens", 0)),
    )


def chat(
    messages: list[dict[str, str]],
    *,
    schema: type[BaseModel] | None = None,
    max_tokens: int = 512,
    tag: str = "",
    temperature: float = 0.2,
    seed: int | None = None,
    use_cache: bool = True,
    endpoint: config.Endpoint | None = None,
    client: httpx.Client | None = None,
    sleep=time.sleep,
) -> ChatResult:
    """Ask the model. ``tag`` names the caller (for tracing and cost per variant).

    With ``schema`` (a pydantic model class) the reply is validated and returned in
    ``ChatResult.parsed``; one retry is made with the error shown to the model, then
    ``SchemaError`` is raised.
    """
    endpoint = endpoint or config.chat_endpoint()
    body = _build_body(endpoint.model, messages, schema, max_tokens, temperature, seed)
    key = _cache_key(endpoint.model, body)
    cache_file = CACHE_DIR / f"{key}.json"

    if use_cache and cache_file.is_file():
        data = json.loads(cache_file.read_text())
        parsed = schema.model_validate_json(_json_text(data["text"])) if schema else None
        return ChatResult(text=data["text"], parsed=parsed, usage=Usage(**data["usage"]),
                          finish_reason=data["finish_reason"], cached=True)

    client = client or http.make_client(endpoint)
    started = time.perf_counter()
    text, finish, usage = _call(client, body, sleep)
    parsed, json_valid = None, True

    if schema is not None:
        try:
            parsed = schema.model_validate_json(_json_text(text))
        except ValidationError as first_error:
            retry = dict(body, messages=body["messages"] + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "That was not valid. Fix it and reply with the JSON "
                                            f"object only. Problem: {str(first_error)[:400]}"},
            ])
            text, finish, usage2 = _call(client, retry, sleep)
            usage = usage + usage2
            try:
                parsed = schema.model_validate_json(_json_text(text))
            except ValidationError as second_error:
                result = ChatResult(text=text, usage=usage, finish_reason=finish, json_valid=False,
                                    latency_ms=int((time.perf_counter() - started) * 1000))
                raise SchemaError(f"schema validation failed twice: {str(second_error)[:300]}",
                                  result) from second_error

    latency_ms = int((time.perf_counter() - started) * 1000)
    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"text": text, "usage": usage.model_dump(),
                                          "finish_reason": finish}))
    return ChatResult(text=text, parsed=parsed, usage=usage, latency_ms=latency_ms,
                      finish_reason=finish, json_valid=json_valid)

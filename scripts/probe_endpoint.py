"""Probe the real chat endpoint (guide 9c.2). Run by a human, with the real key.

    cd Code
    read -s API_KEY; export API_KEY      # key never goes in a file
    python scripts/probe_endpoint.py > probe_report.json

Needs CHAT_BASE_URL, CHAT_MODEL (and CAPSTONE_CA_CERT) in .env or the environment.
Zscaler must be off. Makes about 20 small requests, one at a time. The report holds
status codes and short output snippets only, never headers or the key.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from hr_navigator.llm import config, http  # noqa: E402

PROMPT = [{"role": "user", "content": "Reply with the single word: pong"}]
SCHEMA = {"type": "object", "properties": {"color": {"type": "string", "enum": ["zxq-violet"]}},
          "required": ["color"]}
SCHEMA_MSG = [{"role": "user", "content": "Pick a color. Reply as JSON."}]
TOOL = {"type": "function", "function": {
    "name": "get_days", "description": "Get leave days.",
    "parameters": {"type": "object", "properties": {"kind": {"type": "string"}}, "required": ["kind"]}}}
TOOL_MSG = [{"role": "user", "content": "How many casual leave days do I have? Use the tool."}]


def post(client, body):
    """Single raw request (no retry) so statuses are reported as they are."""
    start = time.perf_counter()
    try:
        r = client.post("/chat/completions", json=body)
    except httpx.HTTPError as exc:
        return {"error": type(exc).__name__}
    out = {"status": r.status_code, "seconds": round(time.perf_counter() - start, 2)}
    try:
        data = r.json()
    except ValueError:
        out["body"] = r.text[:200]
        return out
    if r.status_code != 200:
        out["error"] = str(data)[:300]
        return out
    choice = data["choices"][0]
    msg = choice["message"]
    out.update(
        finish_reason=choice.get("finish_reason"),
        content=(msg.get("content") or "")[:200],
        has_think_tag="<think>" in (msg.get("content") or "") or "</think>" in (msg.get("content") or ""),
        tool_calls=msg.get("tool_calls"),
        has_reasoning_field=any(k in msg for k in ("reasoning_content", "reasoning")),
        completion_tokens=(data.get("usage") or {}).get("completion_tokens"),
    )
    return out


def main() -> None:
    ep = config.chat_endpoint()
    client = http.make_client(ep)
    base = {"model": ep.model, "max_tokens": 200, "temperature": 0.3}
    report: dict = {"model": ep.model}

    report["thinking"] = {
        "flag_false": post(client, {**base, "messages": PROMPT, "chat_template_kwargs": {"enable_thinking": False}}),
        "no_think_switch": post(client, {**base, "messages": [{"role": "user", "content": "/no_think " + PROMPT[0]["content"]}]}),
        "default": post(client, {**base, "messages": PROMPT}),
    }
    off = {"chat_template_kwargs": {"enable_thinking": False}}
    report["structured_output"] = {
        "response_format_json_schema": post(client, {**base, **off, "messages": SCHEMA_MSG, "response_format": {
            "type": "json_schema", "json_schema": {"name": "c", "schema": SCHEMA}}}),
        "response_format_json_object": post(client, {**base, **off, "messages": SCHEMA_MSG,
                                                     "response_format": {"type": "json_object"}}),
        "structured_outputs": post(client, {**base, **off, "messages": SCHEMA_MSG,
                                            "structured_outputs": {"json": SCHEMA}}),
        "guided_json": post(client, {**base, **off, "messages": SCHEMA_MSG, "guided_json": SCHEMA}),
    }
    report["tool_choice"] = {
        mode: post(client, {**base, **off, "messages": TOOL_MSG, "tools": [TOOL], "tool_choice": choice})
        for mode, choice in [("auto", "auto"), ("required", "required"),
                             ("named", {"type": "function", "function": {"name": "get_days"}})]
    }
    seeded = {**base, **off, "messages": [{"role": "user", "content": "Write one sentence about leave."}], "seed": 7}
    a, b = post(client, seeded), post(client, seeded)
    report["seed"] = {"identical": a.get("content") == b.get("content"), "a": a.get("content"), "b": b.get("content")}

    # Does the 1024 cap include reasoning? Think ON with a tiny cap: is the answer cut inside the think block?
    report["cap_includes_reasoning"] = post(client, {**base, "max_tokens": 64, "messages": [
        {"role": "user", "content": "What is 17 * 23? Think step by step."}],
        "chat_template_kwargs": {"enable_thinking": True}})
    report["max_tokens_2000"] = post(client, {**base, **off, "messages": PROMPT, "max_tokens": 2000})

    # Second concurrent request: 429 or queued?
    results: list = []
    slow = {**base, **off, "max_tokens": 300, "messages": [{"role": "user", "content": "Write 150 words about leave."}]}
    t = threading.Thread(target=lambda: results.append(post(httpx.Client(base_url=ep.base_url, verify=ep.ca_cert or True,
                         headers={"Authorization": f"Bearer {ep.api_key}"}, timeout=120), slow)))
    t.start()
    time.sleep(0.5)
    report["concurrent_second_request"] = post(client, {**base, **off, "messages": PROMPT})
    t.join()
    report["concurrent_first_request"] = results[0] if results else None

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

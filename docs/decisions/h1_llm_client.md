# Decision: chat client for Qwen3-8B (`llm/http.py`, `llm/client.py`)

Date: 20 Nov 2026. Status: implemented and tested against a fake server; probes run 20 Nov (results below; concurrency probe to be rerun).

## What it does
`llm.client.chat(messages, *, schema=None, max_tokens=512, tag="", temperature=0.2, seed=None) -> ChatResult`

- Turns thinking off per request (`chat_template_kwargs.enable_thinking=false`), sends `top_k=20`.
- Clamps `max_tokens` to 1024 (course limit).
- Strips `<think>` text from the reply (closed, unterminated, or a stray closing tag).
- With a pydantic `schema`: the schema is described in a system message, the reply is validated, and on failure the model gets one retry that shows the error. After that `SchemaError` is raised; its `.result` keeps the raw text and token counts for the run record (`json_valid=False`).
- Disk cache in `.cache/llm/`, keyed by a hash of model + request body. The key is not in the body, so it is not in the hash or the files. A cache hit does not touch the network or the lock.
- Usage (`tokens_in`, `tokens_out`) comes from the server's `usage` block and is summed over the retry; latency is wall time.

`llm/http.py` (shared with embeddings): per-endpoint CA file, bearer key, retry on 429/503 after 2/4/8 s (or the server's `Retry-After`), one process-wide lock so chat calls run one at a time, error messages that carry the status and server text but never headers.

## Why these choices
- **pydantic model as the schema**: one definition gives the JSON schema for the prompt and the validator; no extra `jsonschema` dependency (pydantic already comes with FastAPI).
- **Schema is both described in the prompt and sent as `response_format`** (the server enforces it; vLLM advises doing both).
- **Raise on schema failure instead of returning a flag**: callers cannot forget to check; the scorer still gets the raw output from the exception.
- **Hand-written `.env` loader**: 10 lines, avoids a dependency.

## Probe results (guide 9c.2), run 20 Nov 2026 on the real endpoint (Qwen/Qwen3-8B, :1033)
| Question | Result | Client setting it controls |
|---|---|---|
| Does `enable_thinking=false` remove the `<think>` block? | VERIFIED: no `<think>` text in any of the 3 variants (flag false, `/no_think`, nothing), 2 completion tokens each. The server appears to run a reasoning parser: the reply has a `reasoning`/`reasoning_content` key, and `content` is clean. Whether that key was non-empty was not checked | none; the client still strips `<think>` as a safety net |
| Does thinking happen at all with the flag ON? | Probably not: with `enable_thinking=true` and a 64-token cap, `content` was already the answer (cut by `length` mid-answer), not reasoning. One sample | none |
| Does the 1024 cap include reasoning? | NOT DETERMINED (no reasoning was produced to measure) | none |
| `response_format` json_schema | VERIFIED enforced: an enum with one unusual value (`zxq-violet`) came back exactly | `USE_RESPONSE_FORMAT = True` |
| `response_format` json_object | Accepted; valid JSON but not schema-constrained (returned "blue") | not used |
| `structured_outputs` | VERIFIED enforced (same result as json_schema) | not needed |
| `guided_json` | Accepted but ignored (returned "blue") | do not use |
| `tool_choice` auto / required / named | VERIFIED: `tool_calls` populated in all three (hermes-style parser), arguments valid JSON. Quirk: with a named function `finish_reason` is `stop`, not `tool_calls` | H5: read `message.tool_calls`, never `finish_reason`. The client has no tool parameters yet |
| Does `seed` make repeated calls identical? | One sample (a short sentence, T=0.3, seed 7): identical. Weak evidence, since a short sentence can repeat by chance | `SEND_SEED = True`; harmless. Check once with two different seeds if exact repeats matter |
| `max_tokens` 2000: clamped or rejected? | VERIFIED rejected: HTTP 400 `output token limit must be between 1 and 1024` | client clamps to 1024 before sending; `mock_llm` now rejects too |
| Second concurrent request: 429 or queued? | NOT DETERMINED: the first request failed with a connect error (probable fault in the probe script, which opened its second connection differently; fixed, rerun needed), so the second request ran alone | keep the lock and the 429 retry |

Speed: single calls took 0.1 to 0.7 s; a 64-token answer took 1.5 s (about 40 tokens/s).

## Unverified / limits
- The tests use a fake server; the probe results above are the only real-endpoint evidence so far (one call per probe).
- The 8192 total-token limit is not checked client-side (needs the Qwen tokenizer). A 400 from the server surfaces as `LLMHTTPError`.
- The lock is per process. Two processes (for example the API and an eval run) with the same key can still collide; the 429 retry covers that.

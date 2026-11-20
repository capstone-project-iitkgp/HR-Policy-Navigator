# Decision: chat client for Qwen3-8B (`llm/http.py`, `llm/client.py`)

Date: 20 Nov 2026. Status: implemented and tested against a fake server; real-endpoint probes NOT yet run.

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
- **Prompt-described schema first, server-side constraint off by default**: it is not known whether the server honours `response_format`. Flip `USE_RESPONSE_FORMAT` in `client.py` after the probes.
- **Raise on schema failure instead of returning a flag**: callers cannot forget to check; the scorer still gets the raw output from the exception.
- **Hand-written `.env` loader**: 10 lines, avoids a dependency.

## Probe results (guide 9c.2) — fill in after running `scripts/probe_endpoint.py`
| Question | Result | Client setting it controls |
|---|---|---|
| Does `enable_thinking=false` remove the `<think>` block? | UNVERIFIED | none (already sent) |
| Is `reasoning_content` returned? | UNVERIFIED | none |
| Does `response_format` json_schema constrain output (200 vs 400 vs ignored)? | UNVERIFIED | `USE_RESPONSE_FORMAT` |
| `structured_outputs` / `guided_json` accepted? | UNVERIFIED | alternative to the above |
| Does `seed` make repeated calls identical? | UNVERIFIED | `SEND_SEED` |
| `tool_choice` auto / required / named: is `tool_calls` populated? | UNVERIFIED | H5 tool-calling approach |
| Does the 1024 cap include reasoning? | UNVERIFIED | `max_tokens` guidance |
| `max_tokens` 2000: clamped or rejected? | UNVERIFIED | mock_llm `limits.py` |
| Second concurrent request: 429 or queued? | UNVERIFIED | mock_llm `--queue`, retry logic |

Run (with Zscaler off): `read -s API_KEY; export API_KEY; python scripts/probe_endpoint.py > probe_report.json`. The report has no key; `probe_report.json` is git-ignored.

## Unverified / limits
- Everything above runs only against a fake server in tests; real Qwen behaviour is unknown until the probes run.
- The 8192 total-token limit is not checked client-side (needs the Qwen tokenizer). A 400 from the server surfaces as `LLMHTTPError`.
- The lock is per process. Two processes (for example the API and an eval run) with the same key can still collide; the 429 retry covers that.

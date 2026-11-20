# H7: tracing (spans, JSONL, trace page)

Status 20 Nov 2026: steps 1 to 4 built, 473 tests pass, offline only. **Not yet run on the real endpoint** (commands at the end). The optional OTLP/Phoenix bridge (step 5) is not built.

## Decisions (Himadri, 20 Nov)
1. JSONL plus a trace page first; OpenTelemetry/Phoenix export is optional and was cut for now.
2. A small standalone trace page is part of H7 (H8 can link to it).

## What was built
- `observability/tracing.py`: a tiny own tracer, no new dependency. `Tracer.span(name, kind, **attrs)` is a context manager; spans nest through a `contextvars` variable, so no function signature carries a span. A span record has `trace_id`, `span_id`, `parent_id`, `name`, `start_ns`, `end_ns`, `status` (ok/error) and `attributes`. An exception inside a span marks it `error` and is re-raised unchanged; `Span.fail(msg)` marks an error without raising (a tool that returned `ok: false`, a blocked call).
- **Off by default.** The global tracer is a no-op unless `TRACE=1` is set or `configure()` is called (`ask.py --trace`, `dev_run --agent --trace`). Eval runs therefore have no extra writes and no timing distortion. `Agent(tracer=...)` overrides it.
- Sink: `JsonlSink` appends one JSON line per span to `.cache/traces/<date>.jsonl` (gitignored; `TRACE_DIR` changes the folder). Sink errors are swallowed and logged once; tracing never breaks the agent.
- **Redaction at write time**: text attributes cut to 500 characters; e-mail addresses, 8+ digit numbers and key-like strings replaced (`[email]`, `[number]`, `[key]`). Employee ids are kept (pseudonyms). Side effect: the placeholder HR contact in an escalation answer shows as `[email]` in a trace; the answer the user sees is unchanged.
- Attribute names follow OpenInference (checked 20 Nov against arize-ai.github.io/openinference): `openinference.span.kind`, `input.value`, `output.value`, `llm.model_name`, `llm.token_count.prompt|completion|total`, `llm.finish_reason`, `tool.name`, `session.id`, `user.id`. `tool.parameters` is UNVERIFIED against the spec. Our own extras use an `app.` prefix.
- `agents/orchestrator.py` spans (one root per question):
  `agent.run` (AGENT) > `plan` > `llm agent_planner`; `executor` > `llm ...`, `tool <name>` (blocked calls appear as error spans); `answer` > `llm agent_answer`, `validate_response`, `critic` > `llm agent_critic`; `escalate_to_hr`. Every model call goes through `Agent._llm`, so `llm/client.py` (shared with Akash) is untouched. `state.step()` and `AgentResponse.steps` are unchanged.
- `observability/summary.py`: `load`, `by_trace`, `summarize` (latency, tokens, model and tool calls, cache hits, blocked calls, error spans, time per stage), `format_summary`, `span_rows`. Stage times are exclusive: `validate_response` and `critic` run inside the `answer` span, so their time is taken out of `answer`. Code-called tools (`validate_response`, `escalate_to_hr`) are not counted as tool calls.
- `scripts/ask.py --trace` prints the trace id and the summary. `evaluation.dev_run --agent --trace` writes one trace per item, stores `trace_id` in each result row and prints mean latency and tokens at the end.
- `app/trace_page.py` (Streamlit, read-only): trace list with filters (day, errors only), metrics, stage table, span tree with duration bars and attributes. Run: `.venv/bin/streamlit run app/trace_page.py`.
- Tests: `tests/test_tracing.py` (10), `tests/test_agent_tracing.py` (7: span tree, escalation, blocked answer, blocked repeat, capacity error, same response with tracing on and off), `tests/test_trace_summary.py` (4), `tests/test_trace_page.py` (6, `AppTest`).

## Deviations from the plan
- Step 5 (OTLP bridge to Phoenix) not built: needs `opentelemetry-sdk` (not installed), Akash has not agreed to Phoenix, and rendering cannot be checked here. No dependency was added to `requirements.txt` for tracing.
- `Tracer.last_trace_id` was added so the CLIs can find the trace they just wrote.
- `RunRecord` has no new field; `trace_id` is only in the `dev_run` rows.

## Known limits
- Whether a JSONL file plus a Streamlit page counts as the brief's "trace dashboard" is an open question for the mentor or Prof; the report should say what it is.
- Redaction is minimal and local; Akash's A7 PII filter could replace it. Traces hold employee questions (redacted, cut to 500 characters) in `.cache/` only; a hand-checked sample is all that should go into `traces/`.
- `dev_run` records no `trace_id` for an item that raised an exception.
- One process writes the file (a lock inside the process); two processes writing at once could interleave lines. Not an issue for the demo.
- UNVERIFIED: the timings on the real endpoint, and how the trace page looks in a browser (only tested headlessly).

## To run (Himadri, Zscaler off)
```
cd Code
read -s API_KEY; export API_KEY
export CHAT_BASE_URL=https://103.42.50.41:1033/v1 CHAT_MODEL=Qwen/Qwen3-8B CAPSTONE_CA_CERT=$PWD/../02_Team_HR_Policy_Navigator/certs/vm05-ca.crt
.venv/bin/python scripts/ask.py "How many casual leave days do I have left?" --employee D010 --trace
.venv/bin/python -m evaluation.dev_run --agent --trace
.venv/bin/streamlit run app/trace_page.py
```
Expected: one trace with root `agent.run`, a `get_leave_balance` tool span, answer, validate_response and critic; per-call tokens add up to the response total; `dev_run`: behaviour and route still 12/12, 12 traces, 0 error spans. Take a screenshot of the trace page for the report and the demo slot.

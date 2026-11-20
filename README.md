# HR Policy Navigator

Multi-agent HR policy copilot for the IIT Kharagpur Executive PG track in Generative and Agentic AI (Track B). The system runs on a shared hosted model of at most 8B parameters. Weights stay fixed. Quality comes from prompting, retrieval, tools, orchestration, and evaluation.

This commit is the repository skeleton and a FastAPI health check. The Week 1 evaluation set and prompt baselines are not in the tree yet.

## Layout

| Path | Purpose |
| --- | --- |
| `src/hr_navigator/llm/` | Endpoint client, JSON validation, retries |
| `src/hr_navigator/prompts/` | Versioned prompt templates |
| `src/hr_navigator/ingestion/` | Corpus manifest loader (hash-checked) and line-based chunker; chunks are exact slices of the source text |
| `src/hr_navigator/retrieval/` | `search(query, k, filters)`: BM25, dense (cached bge-small vectors), RRF fusion; no reranker yet. Decision record: `docs/decisions/a4_retrieval.md` |
| `src/hr_navigator/agents/` | Planner, executor, critic, orchestrator |
| `src/hr_navigator/tools/` | Policy search, leave calculator, HR escalation |
| `src/hr_navigator/state/` | Session memory |
| `src/hr_navigator/safeguards/` | Injection filter for retrieved text, PII redaction of API answers |
| `src/hr_navigator/observability/` | Tracing (spans to `.cache/traces/*.jsonl`, off unless `TRACE=1` or `--trace`), trace summary, redaction |
| `baselines/` | Week 1 single-prompt runners and frozen scores |
| `experiments/` | Ablation configs and raw outputs |
| `evaluation/` | Metrics, failure taxonomy, summary tables; H9: `h9_run.py` (run matrix), `h9_report.py` (tables for the report), doc `docs/decisions/h9_eval.md` |
| `data/manifests/` | `corpus_v1.csv`: document ids, organisations, sources, text checksums |
| `data/eval/` | Frozen queries and, later, retrieval judgments |
| `security_tests/` | `run_attacks.py`: planted-document check and the 12 injection/PII eval items through `/chat` |
| `app/` | Front end; `app/chat_app.py` is the chat page (`streamlit run app/chat_app.py`), `app/trace_page.py` the trace viewer |
| `deploy/` | Dockerfile and how to run with `docker compose up --build` (see `deploy/README.md`) |
| `traces/` | Redacted sample traces |
| `tools/mock_llm/` | Local stand-in for the course LLM endpoint (see [Local LLM](#local-llm-stand-in)) |
| `docs/` | Spec, architecture, corpus datacard, 12-page report |

`data/raw/` and `data/processed/` are rebuilt from manifests and are not committed.

## Setup

Requires Python 3.10 or newer. The latest release (Python 3.15) is the version we use. On macOS: `brew install python@3.15`. There is one virtual environment for the whole repository (API, `hrms/`, front end and tests), created at the repo root as `.venv`. It is not committed; each person creates their own from `requirements.txt`.

```bash
python3.15 -m venv .venv          # any Python 3.10+ also works, e.g. python3.12
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`fastapi` and `streamlit` are pinned together: Streamlit needs a newer Starlette than the FastAPI release we started with (0.115.6) allows, so upgrade them as a pair. After changing pins, run `pip install -r requirements.txt` and `pytest`.

Fill `API_KEY`, `CHAT_BASE_URL`, and `EMBED_BASE_URL` in `.env` when the course endpoint is issued. The health check does not call the model.

## Run

```bash
source .venv/bin/activate
python -m uvicorn hr_navigator.main:app --app-dir src --host 127.0.0.1 --port 8000
```

Run it from this folder with `python -m uvicorn` (not the bare `uvicorn` command) so the `hrms` package is importable. The dummy HR system's endpoints are under `/hrms`; see `hrms/README.md`.

`GET /health` returns:

```json
{"status": "ok"}
```

`POST /chat` takes `{"question", "session_id", "employee_id"}` and returns the agent's answer with citations (rate limit 10 per session per minute, response cache, PII redaction; see `src/hr_navigator/api/chat.py`). `GET /chat/status` shows the retrieval index in use.

Policy search works offline (BM25) with no key. To add embedding search, fill the `EMBED_*` settings in `.env` and run once:

```bash
python -m hr_navigator.retrieval.build --dense
```

Retrieval quality on the frozen eval set: `python -m evaluation.retrieval_eval` (writes `evaluation/retrieval_results.md`). With Docker: `docker compose up --build` (see `deploy/README.md`).

## Local LLM stand-in

The course API is rate limited (1 concurrent request, about 60/min, 8192 tokens). To develop without using it, run a local model in LM Studio and start the stand-in, which emulates those limits behind an OpenAI-compatible URL:

```bash
python -m tools.mock_llm --model llama     # or qwen (not allowed on the office laptop); omit to choose from a menu
```

Then point `CHAT_BASE_URL`, `CHAT_MODEL` and `API_KEY` at it (the command prints the values; `.env.example` has a commented block). Use it for plumbing only, never for evaluation numbers. Full guide: [`tools/mock_llm/README.md`](tools/mock_llm/README.md).

## Test

```bash
source .venv/bin/activate
pytest
```

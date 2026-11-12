# HR Policy Navigator

Multi-agent HR policy copilot for the IIT Kharagpur Executive PG track in Generative and Agentic AI (Track B). The system runs on a shared hosted model of at most 8B parameters. Weights stay fixed. Quality comes from prompting, retrieval, tools, orchestration, and evaluation.

This commit is the repository skeleton and a FastAPI health check. The Week 1 evaluation set and prompt baselines are not in the tree yet.

## Layout

| Path | Purpose |
| --- | --- |
| `src/hr_navigator/llm/` | Endpoint client, JSON validation, retries |
| `src/hr_navigator/prompts/` | Versioned prompt templates |
| `src/hr_navigator/ingestion/` | Loaders and chunkers |
| `src/hr_navigator/retrieval/` | BM25, dense retrieval, fusion, reranker, citations |
| `src/hr_navigator/agents/` | Planner, executor, critic, orchestrator |
| `src/hr_navigator/tools/` | Policy search, leave calculator, HR escalation |
| `src/hr_navigator/state/` | Session memory |
| `src/hr_navigator/safeguards/` | PII, prompt-injection defence, refusals, human review |
| `src/hr_navigator/observability/` | Tracing, cost, and latency |
| `baselines/` | Week 1 single-prompt runners and frozen scores |
| `experiments/` | Ablation configs and raw outputs |
| `evaluation/` | Metrics, failure taxonomy, summary tables |
| `data/manifests/` | Document ids, sources, and checksums |
| `data/eval/` | Frozen queries and, later, retrieval judgments |
| `security_tests/` | Prompt-injection and PII cases |
| `app/` | Front end |
| `deploy/` | Container setup |
| `traces/` | Redacted sample traces |
| `docs/` | Spec, architecture, corpus datacard, 12-page report |

`data/raw/` and `data/processed/` are rebuilt from manifests and are not committed.

## Setup

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `API_KEY`, `CHAT_BASE_URL`, and `EMBED_BASE_URL` in `.env` when the course endpoint is issued. The health check does not call the model.

## Run

```bash
source .venv/bin/activate
uvicorn hr_navigator.main:app --app-dir src --host 127.0.0.1 --port 8000
```

`GET /health` returns:

```json
{"status": "ok"}
```

## Test

```bash
source .venv/bin/activate
pytest
```

# Akash: implementation and test plan (knowledge and platform)

Written Tue 17 Nov 2026. Source plan: [`plan.html`](../../plan.html) (tasks A1 to A7). Code freeze Wed 25 Nov night. Submission Fri 27 Nov.

This file turns tasks A1 to A7 into concrete files, steps and tests. Claude drafts the code; Akash reviews, runs installs, holds the keys and checks documents. Every estimate is unmeasured.

## 1. Before any code: decisions for the Day-1 call (Tue 17)

| # | Decision | My recommendation | Blocks |
|---|---|---|---|
| D1 | Python version | 3.12 for the venv and Docker. 3.10 fails `test_dates_must_be_real_and_are_normalised` (compact ISO dates need 3.11+) | all |
| D2 | How a fresh clone gets the corpus and index | Commit `data/manifests/corpus_v1.csv` plus a `make corpus` script that downloads and rebuilds. Ship the built index (`chunks.parquet`, `dense.npy`, `bm25.pkl`) as a Drive zip for the demo, fetched by a script. Raw files stay out of git (licences) | A2, A3, A6 |
| D3 | Document-ID rule shared with the eval set | `doc_id = <ORG>-<SHORT-TITLE>-<YEAR>`, upper case, for example `INFOSYS-LEAVE-2024`. Himadri maps `gold_sources` in `eval_v1.jsonl` to it | A2, A5 |
| D4 | recall@k level | Report both: document level (gold `doc_id` in top k) and evidence level (a top-k chunk contains the `evidence_quotes` text after whitespace and case normalisation) | A5 |
| D5 | Owner of session storage | Himadri's SQLite store (H5). `/chat` only passes `session_id` through | A6 |
| D6 | Where safeguards sit | `search_policy` returns passages already wrapped and filtered (A7). Redaction runs on the final answer inside `/chat`. The critic (H5) calls `validate_response` | A7 |
| D7 | Reranker runtime | Try `fastembed` (ONNX, no PyTorch) first; use `sentence-transformers` only if fastembed fails on 3.12, Apple silicon or Docker | A4, A6 |

Also on the call: move `eval_v1.jsonl` into `data/eval/` and freeze it (hash and git tag).

## 2. Working method

- One feature branch per task, cut from `main`: `akash/a1-search-stub`, `akash/a2-corpus`, and so on. One PR each; Himadri reviews; merge only when `pytest` is green.
- Each module ends with a short docstring: what it does, what is not verified.
- Before merge, Akash does a 2-minute "explain it back" of the module (Handbook 6.2 control) and adds a line to `docs/ai_use_log.md`.
- Keys live only in `.env`. Tests that call real endpoints carry `@pytest.mark.live` and are skipped by default (`-m "not live"` in `pytest.ini`). Run them on purpose, with Zscaler off: `pytest -m live`.
- New dependencies: Claude names the package and pin; Akash installs it; check it on 3.12 before pinning.

## 3. Shared code layout

```
src/hr_navigator/
  schemas.py              Passage, SearchFilters (shared with Himadri)
  llm/http.py             Himadri: TLS, key, backoff, lock
  llm/embed.py            A1/A3: embed(texts) -> ndarray, batching, disk cache
  ingestion/
    collect.py            A2: downloader (adapted from collect_hr_policies.py)
    manifest.py           A2: manifest read/write, SHA-256 dedupe, validation
    extract.py            A3: PDF/HTML/DOCX -> text + metadata header
    chunk.py              A3: token chunker, two settings
    build_index.py        A3: one command: manifest -> chunks -> embeddings -> index files
  retrieval/
    stub.py               A1: fixture-backed search()
    bm25.py, dense.py     A4
    fuse.py               A4: RRF (k=60)
    rerank.py             A4
    search.py             A4: search(query, k, filters) -> list[Passage]
  safeguards/
    untrusted.py          A7: delimiters, instruction-in-passage filter
    pii.py                A7: redaction
    refusal.py            A7: refusal rules
  api/chat.py             A6: /chat router, rate limit, response cache
evaluation/retrieval_eval.py   A5
tests/                         unit and integration tests (below)
security_tests/                A7 attack suite and results
deploy/Dockerfile, docker-compose.yml   A6
```

## 4. Tasks

Hours are agent / Akash.

### A1. Search stub and endpoint check (Tue 17, 0.3 / 0.5 h)

Steps
1. `schemas.py`: `Passage` with the fields in plan.html section 4, plus `section` and `chunk_index` for citations.
2. `retrieval/stub.py`: `search()` loads `tests/fixtures/passages.json` (about 20 hand-picked passages from the 8 eval source docs) and ranks them by simple word overlap.
3. `llm/embed.py` minimal: one POST to `/v1/embeddings` using `EMBED_BASE_URL`, `EMBED_API_KEY`, `EMBED_CA_CERT`.
4. Reranker install check (D7): time a 10-pair rerank with fastembed on 3.12.
5. Add `.env.example` with variable names only.

Tests
- `tests/test_schemas.py`: `Passage` round-trips to and from JSON; missing required fields raise.
- `tests/test_search_stub.py`: returns at most k items, all `Passage`; a query with words from fixture 3 ranks it first; empty query returns `[]`.
- `tests/test_embed_live.py` (`live`): 2 texts return shape (2, 384), rows have unit norm (within 1e-3).

Done when: stub merged, live test passes once on Akash's laptop, reranker choice written to `docs/decisions/retrieval.md`.

### A2. Corpus build (Wed 18 to Fri 20, 1.5 / 3 h)

Steps
1. Bring `collect_hr_policies.py` and `seed_sources.csv` into `ingestion/` and `data/manifests/` (the Downloads copy is not on this laptop; get it from Drive).
2. `collect.py`: download each seed URL, respect robots.txt, 1 request per second, save to `data/raw/<doc_id>.<ext>`, record HTTP status and SHA-256.
3. `manifest.py`: columns `doc_id, title, org, jurisdiction, category, url, version_date, retrieved_date, licence, sha256, synthetic, status`. Dedupe on SHA-256. Validate: unique `doc_id`, non-empty `org` and `url`, ISO dates.
4. Merge the register CSVs from Jk, Sakshi and Srinivas.
5. Start `data/synthetic/` early: fictional-company policies, `synthetic=true`, written from a template, never copies of real text.
6. Akash checks licence and version for each real document (the slow part).

Tests
- `tests/test_manifest.py`: duplicate SHA-256 rows collapse to one; duplicate `doc_id` fails validation; bad date fails; `synthetic` must be a boolean.
- `tests/test_collect.py`: with `httpx.MockTransport`, a 200 saves a file and a row; a 404 records `status=failed` and does not raise; the same content twice gives one row.
- `tests/test_corpus_manifest.py` (data check, runs on the committed CSV): every `gold_sources` id in `data/eval/eval_v1.jsonl` exists in the manifest.

Done when: `corpus_v1.csv` committed with an honest count of real and synthetic documents in its README line. Gate: at least 150 usable documents by Fri 20.

### A3. Ingestion and index build (Thu 19 to Sat 21, 1.5 / 1 h)

Steps
1. `extract.py`: PDF (`pypdf`), HTML (`trafilatura` or BeautifulSoup), DOCX (`python-docx`). Keep headings as `section`. Drop pages that yield no text and log them.
2. `chunk.py`: count tokens with the BGE tokenizer (`tokenizers`, loaded from the model's `tokenizer.json`). Settings `small = 256/50` and `large = 384/64`. The metadata header counts toward the 512-token cap.
3. `embed.py`: batches of 16, retry 2/4/8 s on 429/503, disk cache keyed by SHA-256 of the text.
4. `build_index.py`: `python -m hr_navigator.ingestion.build_index --chunking small` writes `data/processed/<setting>/chunks.parquet`, `dense.npy`, `bm25.pkl`, `index_meta.json` (manifest hash, chunk count, build time).

Tests
- `tests/test_extract.py`: one tiny PDF, HTML and DOCX in `tests/fixtures/docs/` give expected text and section names.
- `tests/test_chunk.py`: no chunk exceeds the limit, header included; overlap is honoured; a document shorter than one chunk gives one chunk; chunk ids are stable across runs.
- `tests/test_embed.py`: with a fake transport, 40 texts make 3 requests; a cached text makes no request; a 503 then 200 succeeds after one retry; a 401 raises at once.
- `tests/test_build_index.py`: build over the 3 fixture docs with a fake embedder; row counts of `chunks.parquet` and `dense.npy` match; a second build with no changes makes zero embed calls.

Done when: the index rebuilds from the manifest with one command, and the second run is fully cached.

### A4. Hybrid search and reranker (Sat 21 to Sun 22, 1.5 / 1 h)

Steps
1. `bm25.py` (`rank_bm25`, lower-case, simple tokeniser). `dense.py` (cosine on the normalised `.npy`; the query is embedded through the cache).
2. `fuse.py`: Reciprocal Rank Fusion, `score = sum 1/(60 + rank)`.
3. `rerank.py`: cross-encoder over the top 20 fused results, keep the top k.
4. `search.py`: `search(query, k=5, filters=None, mode="hybrid_rerank")`. `mode` is one of `bm25`, `dense`, `hybrid`, `hybrid_rerank` so A5 can compare them. If the embeddings call fails, fall back to BM25 and set a flag in the result.
5. `filters`: `org`, `jurisdiction`, `category`, `synthetic`.
6. Replace the stub behind the same `search()` signature.

Tests
- `tests/test_fuse.py`: hand-worked RRF example; ties are stable; a document in only one list still appears.
- `tests/test_search.py` on the fixture index: an exact phrase query puts the right chunk first in `bm25` mode; `filters={"org": "X"}` returns only org X; `k` is respected; all four modes return `Passage` with the right `scores` keys filled.
- `tests/test_search_fallback.py`: the embedder raises, BM25 results come back with `degraded=True`.
- `tests/test_search_live.py` (`live`): 5 real eval questions return passages from the expected document in the top 5.

Done when: real passages for all 80 eval questions; median latency under 1.5 s (measured, not assumed).

### A5. recall@k harness (Sun 22 to Mon 23, 1 / 1 h)

Steps
1. `evaluation/retrieval_eval.py`: for each question with `gold_sources`, run each mode at k = 1, 3, 5, 10, under both chunking settings.
2. Metrics: document recall@k, evidence recall@k (D4), MRR. Skip `unanswerable` items and report them separately (top score should be low).
3. Exclude the planted-injection document from this index.
4. Write `evaluation/retrieval_results.csv` and a short `evaluation/retrieval_results.md` with the best setting and 3 failure examples.

Tests
- `tests/test_retrieval_metrics.py`: recall@k and MRR on a hand-made 3-question example with known answers; evidence matching ignores case and whitespace; a question with no gold source is skipped and counted.

Done when: the table exists, numbers are reproducible from a cached run, and the chosen setting is recorded in `docs/decisions/retrieval.md`.

### A6. API and Docker (Mon 23 to Wed 25, 2 / 1.5 h)

Start the Docker part on **Mon 23** with stub services, not on Wed 25.

Steps
1. `api/chat.py`: `POST /chat` takes `AgentRequest`, calls the orchestrator (stub until H5 lands), returns `AgentResponse`.
2. Rate limit: in-memory token bucket per `session_id`, 10 requests per minute, `429` with `Retry-After`.
3. Response cache: key = hash of normalised question, `employee_id` and index version; TTL 1 hour; never cache answers that contain a draft action.
4. `deploy/Dockerfile` on `python:3.12-slim`; reranker model downloaded at build time; CA certs mounted read-only from `./certs`; `.env` passed with `env_file`.
5. `docker-compose.yml`: `api`, `ui`, `phoenix`. Health checks on all three.
6. `scripts/fetch_index.sh` for D2.

Tests
- `tests/test_chat_api.py` (FastAPI `TestClient`, orchestrator faked): 200 with a valid body; 422 on a missing `question`; the 11th request in a minute gives 429; the same question twice calls the orchestrator once; a draft answer is not cached.
- Manual, Mon 23: `docker compose up` with stubs; `curl /health` and `/chat` succeed.
- Manual, Wed 25 before freeze: fresh clone into a temp folder, follow the README only, run `docker compose up`, ask one cited question in the UI, see a trace. Fix the README, not the machine, for anything missing.

Done when: the fresh-clone run works before code freeze.

### A7. Safeguards (Tue 24 to Wed 25, 1.5 / 1 h)

Steps
1. `untrusted.py`: wrap each passage in `<passage id=...>` delimiters; flag passages that contain instruction patterns ("ignore previous", "you are now", "system:", tool names) and drop or neutralise them; log each flag.
2. `pii.py`: regex redaction for phone numbers, emails, PAN, Aadhaar and bank numbers in answers; employee data only for the session's own `employee_id`.
3. `refusal.py`: refuse requests for another employee's data, and actions outside the HRMS draft tools.
4. Plant one injection document in a separate test index (`synthetic=true`, `category=security_test`).
5. `security_tests/run_attacks.py`: runs the 6 injection and 6 pii_overreach eval items plus the planted document through `/chat`; writes `security_tests/results.csv` (attack, expected, observed, success).

Tests
- `tests/test_untrusted.py`: each known pattern is flagged; a normal policy sentence that says "ignore" in another sense is not flagged (false-positive check); delimiters cannot be closed early by text inside a passage.
- `tests/test_pii.py`: each PII type is redacted; leave dates and policy numbers are not.
- `tests/test_refusal.py`: asking for another employee's balance is refused; asking for your own is not.
- Attack suite: attack success rate reported; target 0 of 13 succeed, honest number either way.

Done when: the attack table is in `security_tests/` and quoted in the report.

## 5. Test plan summary

| Level | What | How it runs | When |
|---|---|---|---|
| Unit | Each module above | `pytest` (default, offline, no keys) | Every commit; must be green to merge |
| Data checks | Manifest validity, eval-to-manifest ids | `pytest tests/test_corpus_manifest.py` | Each corpus change |
| Live | Real embeddings, real search on 5 questions | `pytest -m live`, Zscaler off, `.env` set | Once per task, and before freeze |
| Retrieval eval | recall@k, MRR across modes and chunking | `python -m evaluation.retrieval_eval` | A5, then after any index change |
| Security | 13 attacks against `/chat` | `python -m security_tests.run_attacks` | A7, then before freeze |
| End to end | Fresh clone, `docker compose up`, one cited answer and one trace | Manual checklist in `deploy/README.md` | Mon 23 (stubs), Wed 25 (real) |

Coverage target: every public function in `ingestion/`, `retrieval/`, `safeguards/` and `api/` has at least one test. No coverage percentage gate.

## 6. Schedule

| Day | Work | Gate |
|---|---|---|
| Tue 17 | Day-1 call (D1 to D7), A1 | Contracts and stub merged |
| Wed 18 to Fri 20 | A2, start A3 | 150+ usable docs; eval set frozen in `data/eval/` |
| Sat 21 | A3 finish, A4 start | Real passages come back |
| Sun 22 | A4, A5 start | Hybrid + rerank merged |
| Mon 23 | A5, A6 Docker with stubs | recall@k table; stub compose runs |
| Tue 24 | A6 real services, A7 | Integrated `/chat` |
| Wed 25 | A7 finish, fresh-clone test | CODE FREEZE |
| Thu 26 | Report numbers, demo script | Results frozen |

Total: about 9 agent hours and 9 to 10 Akash hours, against about 26 hours of capacity. The spare time goes to the synthetic set, earlier Docker work and review.

## 7. Risks specific to this half

- **Corpus count stays low.** Report the real number; labelled synthetic set as insurance; ask the mentor on Sat 21.
- **Embeddings endpoint slow or down.** Disk cache; BM25 fallback; build the full index by Sat 21 so a later outage only affects new queries.
- **Reranker install fails in Docker.** Decided on Day 1 (D7); last-resort cut is to drop it and report honestly.
- **Himadri's UI or tracing late for compose.** Compose starts with stubs on Mon 23; real services swap in when ready.

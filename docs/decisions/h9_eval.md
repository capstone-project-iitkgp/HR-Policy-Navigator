# H9: full evaluation runs and the report (tooling)

Status 20 Nov 2026: tooling built and tested offline with a fake search (507 tests pass). **Nothing has been run on the real endpoint and no result exists.** The real runs need Akash's `hr_navigator.retrieval.search` and the key; every number in the report is UNVERIFIED until then.

## What H9 has to deliver (plan.html, brief)
V2 and V3 on the frozen `eval_v1` (80) plus the 20 `hrms_ext_v1` items, failure-taxonomy labels, a comparison table V0 to V3 with latency and tool-call cost, and the answer to "does the agent beat single-agent RAG". An LLM judge is only for residual cases and is the second cut-list item.

## Run matrix (`python -m evaluation.h9_run`)
| Stage | Variants | Evals | Repeats | Est. time (UNVERIFIED) |
|---|---|---|---|---|
| `main` | `v2_rag`, `v3_agent` | `eval_v1`, `hrms_ext_v1` | 3 | ~95 min |
| `ablate` | `v3_agent_nocritic`, `v3_agent_lenient` (strict_citations off), `v3_agent_noblock` (validate_response reports only), `v3_agent_revise` (critic may revise) | `eval_v1` | 1 | ~90 min |

Estimates use ~2 s per model call (H5 plan) and about 8 calls per V3 question. `--dry-run` prints the plan. V0 numbers come from the H3 records (`results/h3_*.jsonl`), not re-run. Sequential (one request per key). Resumable: re-running the same command skips finished records. Records go to `evaluation/results/h9/<variant>__<eval>.jsonl`; `--official` is always on, so a changed eval file stops the run.
Suggested timing: `main` overnight Tue 24 (V2 first, it is short), ablations Wed 25 only if V3 finished clean, results frozen Thu 26. Cut order if short: ablations to 1 variant, then ablations entirely, then V3 repeats to 2.

## Decisions
1. **Latency is real or it is flagged.** The LLM disk cache has no off switch, so `llm/client.py` (shared with Akash, 1 line) now honours `LLM_CACHE=0`; `h9_run` sets it unless `--use-cache`. The agent counts model calls, cache hits and endpoint latency per run (`AgentState.llm_calls/cache_hits/llm_ms`), and the report warns when any call was replayed.
2. **Each HRMS item runs on its own copy of a freshly seeded database** (`agent_variants.isolated_hrms`): drafts from one question or repeat cannot change another's result, and `HRMS_TODAY` is restored. The user's own `hrms.db` is never touched. Applies to every item with an acting employee or a `today`.
3. **Run records carry an `agent` dict** (behaviour, status, validator issue codes, blocked / report-only block, escalated, revisions, critic verdict, model calls, cache hits, endpoint ms). Empty for v0 variants; the scorer ignores it. The run hash includes critic, strict and block settings, so ablation records are distinguishable.
4. **The scorer is not changed and not tuned again.** It was tuned once on the H3 baselines; the frozen set must not be used to tune it.
5. **No LLM judge built.** The scorer is rule-based and writes uncertain cases to `needs_human.csv`; a judge is worth building only if that residual is large after the first real run (cut-list item 2).
6. **Failure labels stay suggestions.** `taxonomy_sheet.csv` lists every failed question at repeat 0 with the scorer's suggested label and an empty `human_label` column. Himadri confirms before the report quotes any count.

## Report (`python -m evaluation.h9_report`)
Reads `results/h9` plus the H3 baselines, writes `results/h9/report/`: `report.md`, `scores_<variant>__<eval>.csv`, `needs_human.csv`, `taxonomy_sheet.csv`. Sections per eval set: headline (strict pass with Wilson 95% CI, lenient, SD across repeats, any@5, MRR); pass by question type; suggested failure labels; latency and cost (median, p95, tokens in/out, model calls, tool calls, steps, cache hits); checks and agent behaviour (blocked answers, report-only blocks, escalations, revisions, critic verdicts, top validator issues); HRMS tool use (right tools, right arguments, forbidden calls, wrong state); paired comparisons (McNemar exact, bootstrap CI) V0 to V2, V2 to V3 and every ablation against V3. The headline sentence "does the agent beat plain RAG" is computed from the paired test and says "no reliable difference" unless p < 0.05.

## Tests
`tests/test_h9.py` (19): plan, preflight, dry run, cache switch, agent facts in the record, blocked / report-only, revise, cache-hit counting, HRMS isolation (including that the user's DB and date are restored), report end to end on relabelled demo records, replayed-latency warning. The report on the real H3 records reproduces the H3 baseline (0.16, 0.18, 0.19).

## Not verified / known limits
- All real-run numbers, the time estimates and the retrieval-dependent metrics (any@5, MRR) for V2 and V3.
- `isolated_hrms` seeds with the default date, then sets `HRMS_TODAY` per item; the seeded data already holds some drafts (for example D010 on 1 Dec), so some draft requests may legitimately overlap them.
- Ablations at 1 repeat have wide intervals; read them as hints.
- The eval sets are DRAFT until the human review of the answers is finished.
- `agent_facts` finds the critic verdict and blocks by reading the step trace text; if those trace lines are reworded, `tests/test_h9.py` fails.

## To run (Himadri, Zscaler off, after `retrieval.search` exists)
```
cd Code
read -s API_KEY; export API_KEY
export CHAT_BASE_URL=https://103.42.50.41:1033/v1 CHAT_MODEL=Qwen/Qwen3-8B CAPSTONE_CA_CERT=$PWD/../02_Team_HR_Policy_Navigator/certs/vm05-ca.crt
.venv/bin/python -m evaluation.h9_run --dry-run --stage all
.venv/bin/python -m evaluation.h9_run --stage main --variants v3_agent --evals eval_v1 --repeats 1   # smoke test first
.venv/bin/python -m evaluation.h9_run --stage main
.venv/bin/python -m evaluation.h9_report
```

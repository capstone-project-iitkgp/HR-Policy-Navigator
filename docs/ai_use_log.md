# AI-use log (Handbook 6.2)

What Claude drafted, who reviewed, and when. Add one row per module or PR.

| Date | Module | Drafted by | Reviewed by | Notes |
|---|---|---|---|---|
| 16 Nov 2026 | `hrms/`, `tools/mock_llm` | Claude | Himadri; Akash (PR #1) | Merged |
| 20 Nov 2026 | `schemas.py`, `llm/config.py`, `llm/http.py`, `llm/client.py`, `scripts/probe_endpoint.py`, `tests/test_llm_client.py` | Claude | Himadri (author), Akash (PR review) pending | Tested on a fake server; probes run 20 Nov (results in `docs/decisions/h1_llm_client.md`), concurrency probe to be rerun |
| 20 Nov 2026 | `evaluation/` (records, evalset, variants, run_eval, score), `scripts/freeze_eval.py`, tests | Claude | Himadri (author), Akash (PR review) pending | Scorer ported from `score_eval.py`; tested with demo variant only; thresholds not yet tuned on real output |
| 20 Nov 2026 | `evaluation/baselines.py`, `tests/test_baselines.py` | Claude | Himadri (author), Akash (PR review) pending | H3 v0_single / v0_fewshot / v0_cot, closed-book; not yet run on the real endpoint |
| 20 Nov 2026 | `docs/h3_baselines.md`, `score.py` (4 refusal markers) | Claude | Himadri (author) pending | Numbers from the 20 Nov real-endpoint run; markers tuned on baseline output |
| 20 Nov 2026 | `prompts/` (4 prompts, schemas, loader, citation check), `evaluation/dev_run.py`, `scripts/build_dev_set.py`, `data/dev/dev_v1.jsonl`, tests | Claude | Himadri (author), Akash (PR review) pending | H4 v1; not yet run on the real endpoint. Dev set is AI-drafted, review pending |
| 20 Nov 2026 | `llm/client.py` (tools, tool_choice, tool_calls), `schemas.ChatResult.tool_calls`, `docs/plans/h5_agent_loop_plan.html`, tests | Claude | Himadri (author), Akash (PR review) pending | H5 step 1; tool calls verified only against a fake server, not yet the real endpoint |
| 20 Nov 2026 | `agents/state.py`, `agents/budget.py`, `tests/test_agent_state_budget.py` | Claude | Himadri (author), Akash (PR review) pending | H5 step 2; token estimate is chars/3, not a real tokenizer |
| 20 Nov 2026 | `agents/toolbox.py`, `tests/fake_search.py`, `tests/test_toolbox.py` | Claude | Himadri (author), Akash (PR review) pending | H5 step 3; policy_search takes an injected search function, tested only with a keyword fixture |
| 20 Nov 2026 | `state/session_store.py`, `tests/test_session_store.py` | Claude | Himadri (author), Akash (PR review) pending | H5 step 4; SQLite memory, 6 turns, 500 chars, bound to employee id |
| 20 Nov 2026 | `agents/orchestrator.py`, `tests/test_orchestrator.py` | Claude | Himadri (author), Akash (PR review) pending | H5 step 5; loop tested only with a scripted fake chat(); Qwen tool-call behaviour unverified |
| 20 Nov 2026 | `tests/test_orchestrator.py` (26 failure-mode tests), orchestrator fixes (None answer, HRMS-only answers need no citation) | Claude | Himadri (author), Akash (PR review) pending | H5 step 6; all on a scripted fake chat() |

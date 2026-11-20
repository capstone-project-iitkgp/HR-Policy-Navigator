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

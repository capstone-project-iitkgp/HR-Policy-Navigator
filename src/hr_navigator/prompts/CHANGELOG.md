# Prompt change log

One line per version: what changed, why, and the evidence. Hashes are in each run record (`prompt_hash`).

| Date | Prompt | Version | Change | Evidence |
|---|---|---|---|---|
| 20 Nov 2026 | planner, executor, critic, answer | v1 | First versions, written from the H3 baseline failures: answer when the passages support it (116-134 of 150 answerable questions were over-refused), cite by passage id with code-checked quotes (fewshot invented quotes), explicit clarify / conflict / injection definitions (0-6 of 18 passed) | `docs/h3_baselines.md`. Not yet run on the real endpoint |

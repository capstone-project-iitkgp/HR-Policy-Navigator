# Decision: prompts (H4)

Date: 20 Nov 2026. Status: set frozen as planner v3, executor v2, critic v2, answer v2. Full history and evidence: `src/hr_navigator/prompts/CHANGELOG.md`.

- Four prompts, versioned files in `src/hr_navigator/prompts/`, rendered with `{{placeholders}}`; outputs are pydantic schemas (`prompts/schemas.py`) enforced by `json_schema`.
- Written from the H3 baseline failures: over-refusal (answer when the passages support it), invented quotes (cite by passage id, quote checked in code), weak clarify / conflict / injection handling (explicit definitions).
- Code checks do the verification, not the model: `render.check_citations`, `render.unsupported_numbers`, `render.snap_quotes`.
- The critic is advisory. Its false "revise" on correct drafts (D04, D12) is the weak part; H9 measures it.
- `flag_conflict` is decided at the answer stage (a conflict is only visible after retrieval), not by the planner.
- Evidence is 12 dev items written alongside the prompts: a sanity check, not a result. The frozen eval set (`eval_v1`) is not used for tuning.
- Changes after real-endpoint runs: executor v2 (sees question and today, DONE is text, never guess a request id).

# Prompt change log

One line per version: what changed, why, and the evidence. Hashes are in each run record (`prompt_hash`).

| Date | Prompt | Version | Change | Evidence |
|---|---|---|---|---|
| 20 Nov 2026 | planner, executor, critic, answer | v1 | First versions, written from the H3 baseline failures: answer when the passages support it (116-134 of 150 answerable questions were over-refused), cite by passage id with code-checked quotes (fewshot invented quotes), explicit clarify / conflict / injection definitions (0-6 of 18 passed) | `docs/h3_baselines.md`. Not yet run on the real endpoint |
| 20 Nov 2026 | planner | v2 | Removed the `flag_conflict` route (a conflict between passages can only be seen after retrieval, so the planner cannot choose it); added a clarify example for a missing leave type | First dev run (v1): D08 "how many days of leave can I take?" was routed to policy and refused as not covered |
| 20 Nov 2026 | answer | v2 | `flag_conflict` now says to compare passages and when it applies; `clarify` mentions missing leave type and forbids using refuse_not_in_corpus for it; injection refusals must still answer the real question with citations | First dev run (v1): D08 refused instead of clarifying; D09 refused the injection but did not give the probation answer; D12 (as first written) answered. D12 was a badly built item (an exception, not a conflict) and was replaced; D03's fact check was a spelling mismatch ("month's salary") and was corrected |

First dev run, v1, 12 items: behaviour 10/12, route 10/12, 0 citation problems, 0 errors; critic flagged D08 (right). Prompt sizes (tokens in): planner 709, answer 1127, critic 673. Dev items are few and were written alongside the prompts: a sanity check, not a result.
| 20 Nov 2026 | planner | v3 | Clarify is not used when the question names the leave type or case | Second dev run (v2): D04 (compensatory off, a named type) was routed to clarify; the answer step still got it right |
| 20 Nov 2026 | critic | v2 | Receives the draft's behaviour; for non-answer drafts it checks only rules 3 and 4; no extra advice or wording requests | Second dev run: the critic marked 2 of 2 correct non-answers "revise" (D08, D12) with issues that were not in the draft or were already in it |

Second dev run, v2 prompts: behaviour 12/12, route 11/12, 0 citation problems. The critic is the weak part: false "revise" on both correct non-answer items. D09 still refuses the injection without giving the probation figure (not fixed; no more tuning on 12 items).

Third dev run (planner v3, critic v2): behaviour 12/12, route 12/12, 1 citation problem (D02: a near-verbatim quote with a word dropped; the code check caught it), 0 errors. The critic still said "revise" for two correct drafts (D04, D12), asking for details the question did not need. Decision: prompts v1 set is frozen as planner v3, executor v1, critic v2, answer v2. The critic's verdict is advisory; invented numbers are checked in code (`render.unsupported_numbers`), and the value of the critic is measured in H9 (V3 with and without it).

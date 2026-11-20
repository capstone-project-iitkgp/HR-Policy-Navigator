# Decision: the agent loop (H5)

Date: 20 Nov 2026. Status: implemented, tested (412 tests), run on the real endpoint with `scripts/ask.py` and `dev_run --agent`. Plan: `docs/plans/h5_agent_loop_plan.html`.

## What it is
Plain code, no framework: `agents/orchestrator.py`. Flow: load session memory and the employee profile (by code) -> PLAN (planner v3) -> EXECUTE loop (executor v2, native tool calls) -> ANSWER (answer v2) -> code checks and one revision -> CRITIC (critic v2, advisory). `Agent.run()` never raises: capacity and network problems become `status="error"`.
Parts: `agents/state.py` (state, step counter, trace), `agents/budget.py` (token fit), `agents/toolbox.py` (tools), `state/session_store.py` (memory), `llm/client.py` (tool support), `evaluation/agent_variants.py` (variants `v2_rag`, `v3_agent`, `v3_agent_nocritic`).

## Decisions (accepted 20 Nov)
1. Step = one planner, executor, tool, revision or critic action. Cap 10. At the cap, go to the answer (trace `step_cap`). The answer stage is not counted.
2. Critic `off | advisory | revise`, default advisory. It flags correct drafts (D04, D12); H9 measures V3 with and without it.
3. `strict_citations`: an answer with no citation that verifies becomes `refuse_not_in_corpus` with a fixed message. Possible cost in recall; H9 measures it.
4. Tools offered depend on the route (policy: `policy_search`; hrms: 7 HR tools; policy_and_hrms: both). Other routes get none.
5. First executor call uses `tool_choice="required"`, later calls `"auto"`.
6. Session memory: last 3 turns (about 300 tokens), marked untrusted, prepended to the question. Store keeps 6 turns, each cut to 500 characters.
7. Identity: `actor_id` comes from the request only. `employee_id`, `emp_id`, `actor_id`, `actor`, `user_id` are removed from model arguments.
8. `v2_rag` (one search by code, answer, checks, critic off) and `v3_agent` are registered here.

## Where the build differs from the plan
- `ToolBox.run` returns `ToolResult(result, passages)`: the model sees 300-character snippets, the answer stage gets the full passages.
- `policy_search` takes only `query`. `escalate_to_hr` and `validate_response` were added in H6 as code-called tools (see `h6_tools.md`); the route `escalate` has no tools and the hand-over is made by code after the answer.
- The clarify and refuse_injection routes do one search by code, so the answer can name options or still answer the real question.
- Code checks added: `snap_quotes` (a quote that is at least 85% like a passage sentence is replaced by the passage's own text), `unsupported_numbers` treats 6 and 6.0, 1,000 and 1000 as equal. An unsupported number alone is noted, not blocked (false positives).
- HR-data-only answers need no citation; model citations are dropped with a note.
- Token budget: characters / 3 (deliberately high; no Qwen tokenizer). Context 8192, output reserve 700, tool results at most 40% of the free space, passages best first, the last one shortened (min 200 characters) or dropped.
- Session store limit: "no PII beyond employee id" holds only as far as the employee does not type PII into a question.

## Rules the loop enforces (all from real runs)
- A tool named DONE ends the loop (Qwen sometimes "calls" it).
- A repeated call is blocked; the second repeat leaves the loop. Two failed tool calls in a row leave the loop.
- A search that adds no new passage, or 2 empty searches, withdraws `policy_search` from the tools offered (the result tells the model). The loop goes on with the HR tools; with no tools left it ends.
- `cancel_leave_request` is blocked until `list_my_requests` has succeeded (the model invented id 12345).
- `needs_confirmation` only when a draft was created (or the model says draft and no cancel-only turn); a cancel alone is `answered`.
- Executor text with no tool call and no DONE is nudged once.

## What the real runs showed (Qwen3-8B, 20 Nov)
- Tool arguments are valid JSON and routing is sensible. Without question and today's date the executor made up dates (fixed: executor v2). `required` made it guess ids (fixed: list-first guard).
- Dev set: behaviour 12/12, route 12/12, 0 citation problems, 0 errors. This is a sanity check only: items were written with the prompts, passages are hand-picked, some calls were cached.
- Cost: about 4,000 tokens in for a policy question, 7,700 to 11,000 for HR and mixed questions; 4 to 8 calls.

## Known gaps (UNVERIFIED or open)
- Real retrieval is not tested: `search()` is Akash's and not ready; runs used a search that returns nothing or the dev item's passages.
- One wasted nudge call on HR questions (executor writes a sentence instead of DONE). Possible fix: treat text as done after one successful tool call.
- A mixed question with no policy passages can be answered from HR data without saying the policy was not found.
- A 2-search plus 2-HR-call question uses 9 of 10 steps; watch `step_cap` in H9.
- Re-running apply-leave adds duplicate drafts in the local HRMS DB; reset the DB between runs and repeats (H9, esp. `hrms_ext`).
- Untested on the real endpoint: injection inside a tool-using question, `hrms_ext` items through the runner.
- Concurrency (429 or queue) probe still to re-run.
- For Akash: `ChatResult.tool_calls` is a new field; the tool is named `policy_search`; `search(query, k, filters)` is injected into `ToolBox`.

# Dummy HRMS: data card and design decisions

Status: draft, written 16 Nov 2026 with AI assistance (see "AI-use disclosure"). Component code is in `hrms/`;
how to run it is in `hrms/README.md`.

## Purpose

The agent has to combine policy (retrieved from the HR corpus) with facts about the person asking (balances,
manager, requests). The mentor asked for a dummy database of about 40-50 records (session 1, 14 Nov) and, time
permitting, an apply-leave flow with guardrails and an audit trail. `hrms/` is that system.

## The data is synthetic

- 48 fictional employees (24 per organisation), invented names, `@example.org` addresses, no real person or company.
- Policies are real documents from the corpus; the *people* and *balances* are made up.
- Edge-case employees are hand-written to match evaluation questions (for example D010 and B010 both joined on
  18 March: no March credit at DEF, half credit at BLDEDU). Filler usage numbers come from a fixed random seed.
- The data lives in `hrms/seed_data/*.csv` (the source of truth). The SQLite file is generated and not committed.
- "Today" in the system is fixed to 2026-11-16 (`HRMS_TODAY`) so demos, tests and evaluation answers are repeatable.

## Rule sources

Two policy sets, one per organisation, each rule citing its section (`hrms/rules.py`, field `ref`):

| Organisation | Corpus document | Used for |
|---|---|---|
| DEF | DOC-008, Digital Empowerment Foundation HR Policy Manual v2.0, sections 4, 5 | casual, sick, earned, comp-off, maternity, paternity leave; notice period; sandwich rule; cancel / modify; holidays |
| BLDEDU | DOC-009, BLDE Association Revised HR Policy 2020, leave chapter | earned, casual, commuted, special casual, bereavement, maternity leave; probation; carry-forward caps |

## Where the sources conflict, and what the system does

The system has to pick one value; the evaluation set still expects the conflict to be flagged where it is a
question (eval_v1 Q048). The choice is noted in `rules.py` (`note`).

| Topic | Conflict inside BLDEDU | Value used |
|---|---|---|
| Earned leave per year | section 5.A: 10 days (teaching), 30 (non-teaching); "Leave Entitlements" table: 30 for everyone | the section values |
| Casual leave per year | table: 15 days; elsewhere: "7 days for every half yearly" | 15 (table) |
| Commuted leave per year | section 5.C: 20 days per completed year; table: 10 | 10 (table) |
| Probation | one section gives probationers 1 day a month, another says entitlement starts only on confirmation | teaching probationers accrue 1 day a month; non-teaching probationers get none |
| Maternity | "applicable only up to two existing child" (ambiguous) | read as fewer than 2 existing children (UNVERIFIED) |

## Other assumptions and limits (all UNVERIFIED readings)

- **2026 holiday dates are approximate.** Movable festivals (Holi, Eid, Diwali and others) were written from
  memory. The counts and types follow the policies; do not quote the dates as real.
- Back-dating is measured from the start date. Restricted holidays count as working days. The sandwich rule is
  applied to weekly offs only. A sick-leave certificate is required once 2 or more sick days are used this year.
- Leave taken before the system's start is a number in the balance, not individual requests.
- A request must fall inside the current calendar year.
- Not modelled: Leave Without Pay, loss of pay during notice (leave is refused instead), partial cancellation,
  comp-off validity windows, abortion / adoption / special disability leave, study and duty leave.

## Safety design (the human-in-the-loop boundary)

1. The agent can read the logged-in employee's own data, check a request against the policy, save a **draft**,
   and discard a draft. Nothing else.
2. Filing a request, cancelling a filed or approved one, and changing an approved one need the employee's own
   confirmation in the app (`confirmed=True`, set only by the Confirm buttons). The agent tools never pass it.
3. Approving, rejecting or sending back is done by the approver in the manager screen. The agent has no tool for it.
4. The acting employee comes from the session (the app, or `HRMS_ACTING_EMPLOYEE` for MCP), never from the model.
   Tools reject any extra argument such as `emp_id`, so a prompt injection cannot switch identity.
5. Every call is written to `audit_log` with the actor, action, request id, result and channel (`app` or `agent`).

These are tested in `hrms/tests/` (access rules, confirmation gates, agent tool schemas, MCP server).

## Evaluation extension

`data/eval/hrms_ext_v1.jsonl` adds 20 items for the HRMS tools (lookups, eligibility and date calculations,
drafts, blocked actions, over-reach, an injection attempt, an ambiguous request). It is separate from the frozen
`eval_v1` set so that set and its hash stay unchanged.

- Each item names the employee it is asked as (`acting_employee`) and a fixed `today`.
- `verify` steps run the real tool and compare the gold numbers with the seeded database;
  `expected_calls` replay the ideal tool calls and compare the resulting state with `state_after`. Both run in
  `hrms/tests/test_eval_items.py`, so a wrong gold number or a seed-data change fails the test suite.
- Policy quotes in `evidence_quotes` were checked against the saved source text when the file was built.
- Status of every item is **DRAFT** (AI-assisted, no human reviewer yet). The wording and the policy reading
  need review before the set is frozen; then set `reviewer`, `review_date` and `status`, record the file hash
  and tag the repository.

## AI-use disclosure (Student Handbook 6.2)

- Tool: Claude Code (Anthropic).
- Purpose and extent: the plan, code, seed data, tests, documentation and the draft evaluation items for the
  `hrms/` component were written by Claude Code in conversation with Himadri, who chose the scope and design
  (two organisations with real rules, draft-only agent actions, manager approval, MCP wrapper) and reviews the work.
- The mentor allowed AI coding tools on 14 Nov (verbally, with the instruction to verify the output and keep the code
  simple). That is not a written waiver of the handbook. The disclosure above should be repeated in the report, and
  the question raised with the mentor in session 2.
- Human review still to do: read every function well enough to explain it, check the policy readings above, review
  the evaluation items, and verify the holiday dates.

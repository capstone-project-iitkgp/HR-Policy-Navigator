# Dummy HRMS / leave management

A small, **fully synthetic** HR system that the HR Policy Navigator agent calls as tools
(leave balance, holidays, manager lookup, and later apply/cancel leave). It exists so the
agent can combine *policy* (from the RAG corpus) with *employee facts* (from a database),
as the mentor asked on 14 Nov. No real person or company data is used.

Status: **All planned phases done** (draft versions): schema, rules, seed data, read functions, request
validation, draft / submit / cancel / modify, manager decisions, HTTP API, Streamlit demo page, agent tool
wrappers, MCP server, 20 evaluation items and tests. Design decisions, data caveats and the AI-use disclosure
are in `docs/decisions/hrms_design.md`. Still needed from a human: review of the evaluation items and of the
policy readings, and a check of the holiday dates.

## Quickstart

Run everything from the `Code/` folder, with the virtual environment from the project README active
(`python3.15 -m venv .venv && source .venv/bin/activate`).

```bash
pip install -r requirements.txt          # no extra packages needed for Phase 1
python -m hrms.seed                      # (re)builds hrms/hrms.db from hrms/seed_data/*.csv
pytest                                   # runs tests/ and hrms/tests/
```

The database file is **not** in git. It is built automatically the first time any function
needs it, so a fresh checkout works without a seed step. Run `python -m hrms.seed` any time
to reset it to the clean starting state (demo actions write to it).

```python
from hrms import service
service.get_leave_balance("D010", leave_code="EL")   # D010 asks about their own earned leave
service.get_manager("D010")
service.get_leave_balance("D003", "D010")             # a manager reading a report's balance
```

## Functions (`hrms/service.py`)

All take `actor_id` first and return `{"ok": ..., ...}`. `confirmed` is set only by the employee's own
confirmation in the app; the agent tools will never pass it.

| Function | What it does |
|---|---|
| `get_employee`, `get_manager`, `get_leave_balance`, `get_holidays`, `list_requests` | Read data the actor may see |
| `check_leave_request(actor, code, start, end, half_day)` | Test a request against the policy and return `valid`, `errors`, `warnings`, days, balance after, approver, whether a document is needed. Saves nothing |
| `create_leave_draft(...)` | Save a DRAFT if it passes the checks. Does not touch the balance |
| `submit_request(actor, id, confirmed, doc_attached)` | DRAFT or SENT_BACK to PENDING; the days move into `pending` |
| `cancel_request(actor, id, confirmed)` | Drafts cancel freely; PENDING and APPROVED need `confirmed`, APPROVED only until its end date |
| `modify_request(actor, id, start, end, half_day, confirmed)` | Change dates and re-check. APPROVED goes back to PENDING (needs `confirmed`; at DEF the new start must be inside the old dates) |
| `preview_request(actor, id)` | Re-check a saved request of your own (it ignores itself when looking for overlaps). Saves nothing |
| `list_pending_approvals(actor)` | The actor's inbox: pending requests they decide, with the employee's balance |
| `decide_request(actor, id, decision, note)` | `APPROVE`, `REJECT` or `SEND_BACK` a PENDING request. Only the approver (the reporting manager, or HR for someone with no manager); reject and send back need a note |

```
DRAFT --submit--> PENDING --approve--> APPROVED
  |                  |  \--reject--> REJECTED
  |                  \--send back--> SENT_BACK --submit--> PENDING
  \--cancel--> CANCELLED    (PENDING and APPROVED can be cancelled too)
```

Employee actions (`submit`, `cancel`, `modify`) and manager actions (`decide`) are separate on purpose:
the agent tools will get only the read functions, `check_leave_request`, `create_leave_draft` and
`cancel_request` without `confirmed`. Nothing the agent can call files or approves leave.

## HTTP API (`hrms/api.py`, mounted at `/hrms`)

Start the app from `Code/`: `python -m uvicorn hr_navigator.main:app --app-dir src`, then open `/docs`.
Every call needs the header `X-Employee-Id: D010` (a **demo login**, trusted as is; replace with real
authentication for anything real). The id is never read from the body.

| Method and path | Service function | Errors |
|---|---|---|
| `GET /me`, `/employees/{id}`, `/balance`, `/holidays`, `/manager`, `/requests` | the read functions | 401 unknown user, 403 not allowed, 404 not entitled |
| `POST /leave/check` | `check_leave_request` | always 200; read `valid` |
| `POST /requests` | `create_leave_draft` | 201, or 422 with `errors` |
| `POST /requests/{id}/submit`, `/cancel`, `/modify` | the same-named functions | 409 `confirmation_required` until the body has `"confirmed": true`; 404 if not yours |
| `GET /approvals`, `POST /requests/{id}/decision` | `list_pending_approvals`, `decide_request` | 404 if you are not the approver, 422 if a note is missing |

```bash
curl -H "X-Employee-Id: D010" "localhost:8000/hrms/balance?leave_code=EL"
```

Checks applied: leave type open to the employee, valid dates within this year, no leave during notice,
half-day rules, minimum and maximum days, advance notice, back-dating limit, no advance sick leave,
balance (commuted leave counts double), no overlap with another active request, DEF sandwich rule,
holidays and weekly offs not counted (maternity counts calendar days), supporting documents.

## Agent tools (`hrms/agent_tools.py`)

What the planner / executor gets. Give the model `tool_schemas()` (OpenAI function-calling format, about
1,050 tokens) and run each call it makes with `run_tool(actor_id, name, arguments)`. `actor_id` is the
logged-in employee from the app session; `arguments` may be a dict or the model's JSON string.

```python
from hrms.agent_tools import tool_schemas, run_tool
run_tool("D010", "get_leave_balance", '{"leave_code": "EL"}')
# {"ok": True, "as_of": "2026-11-16", "balances": [{"leave_code": "EL", ..., "available": 6.0}]}
```

| Tool | Purpose |
|---|---|
| `get_my_profile` | Organisation, category, join date, status, and today's date |
| `get_leave_balance(leave_code?)` | Own balances this year |
| `get_holidays(holiday_type?)` | Own organisation's calendar |
| `get_my_manager` | Who approves the employee's leave |
| `list_my_requests(status?)` | Own requests, newest first, at most 10 |
| `check_leave_request(leave_code, start_date, end_date, half_day?)` | Would this be allowed? Days, balance after, warnings, document needed, approver |
| `create_leave_draft(..., reason?)` | Save a **draft** only; the result tells the agent the employee must confirm in the app |
| `cancel_leave_request(request_id)` | Discard a draft; filed or approved requests answer `confirmation_required` |

Guarantees (each has a test):
- No tool takes an employee id, and extra arguments such as `emp_id` or `confirmed` are rejected before they
  reach the HR system, so the model cannot change whose data it acts on or confirm for the employee.
- There is no tool to submit, change an approved request, approve, reject or list another person's data.
- Bad input never raises: the agent gets `{"ok": False, "error": "bad_arguments", "problems": [...]}` (or
  `unknown_tool`, `tool_failed`) and can correct itself. Results are trimmed to keep the 8,192-token window free.
- Every call is written to the audit log with `channel = "agent"` (screens and the API use `"app"`).

Prompt hint for the agent: tell it today's date comes from `get_my_profile`, that it must never ask for an
employee id, and that after `create_leave_draft` it should tell the employee to confirm in the app.

## MCP server (`hrms/mcp_server.py`)

The same 8 tools over the Model Context Protocol (stdio), so any MCP client can use them: the capstone agent,
the MCP Inspector, or a desktop assistant for the live demo. It is a thin wrapper: every call goes through
`agent_tools.run_tool`, so the argument checks, the trimmed results and the `agent` audit channel are identical,
and the server lists exactly the schemas the in-process tools use.

```bash
HRMS_ACTING_EMPLOYEE=D010 python -m hrms.mcp_server       # from Code/, with the venv active
```

The employee the tools act as is fixed at start-up by `HRMS_ACTING_EMPLOYEE` (like a login), never by a
tool argument. The server exits with a message if it is missing or not an employee. Client configuration
(adjust the paths):

```json
{"mcpServers": {"hrms": {
  "command": "/Users/you/.../Code/.venv/bin/python",
  "args": ["-m", "hrms.mcp_server"],
  "cwd": "/Users/you/.../Code",
  "env": {"HRMS_ACTING_EMPLOYEE": "D010"}}}}
```

It uses the MCP Python SDK **2.x** (`mcp==2.3.0`, low-level `Server`); most examples online are for 1.x
(`FastMCP`) and will not match. Tool failures come back as results with `is_error` set, carrying the same
`{"ok": False, "error": ..., "message": ...}` body, so the model can correct itself.

## Demo page (`hrms/demo_app.py`)

```bash
python -m streamlit run hrms/demo_app.py        # from Code/, with the venv active
```

Pick anyone in "Log in as" (the demo login). Tabs: **My balances**, **Apply for leave** (check against policy,
save a draft), **My requests** (confirm and submit, withdraw, cancel approved leave, change dates) and **Holidays**.
**Approvals** is the manager's inbox (approve, reject, send back; a note is needed to reject or send back).
The sidebar shows the person, a notice-period banner, a "Reset demo data" button and the recent audit log.
The page calls `hrms/service.py` directly, and only its Confirm buttons pass `confirmed=True`.

Suggested 2-minute demo: D010 checks a Friday-to-Monday casual leave (sandwich warning), saves it, confirms;
switch to D003 and approve it; switch back to D010 and see the balance move; D020 tries casual leave with zero
balance; D015 is serving notice. Press "Reset demo data" before every rehearsal.

## Evaluation items (`data/eval/hrms_ext_v1.jsonl`)

20 draft items for the HRMS tools, separate from the frozen `eval_v1` set. `hrms/tests/test_eval_items.py` runs
each item's `verify` steps against the seeded database and replays its ideal tool calls, so the gold numbers
are checked by the test suite, not trusted. Items are `DRAFT` until a human sets `reviewer` and `review_date`.

## Folder map

| File | Purpose |
|---|---|
| `rules.py` | Leave rules for both organisations. **Single source of truth**, each rule cites its policy section. |
| `schema.sql` | Tables: employees, leave_types, leave_balances, leave_requests, holidays, audit_log. |
| `db.py` | Connection helper, DB path, the fixed "today". |
| `seed.py` | Builds the DB from `schema.sql`, `rules.py` and the CSVs. |
| `service.py` | Functions the tools call. Every call checks who is asking and writes an audit row. |
| `api.py` | HTTP endpoints under `/hrms`, mounted by `src/hr_navigator/main.py`. |
| `demo_app.py` | Streamlit demo page (employee screens and manager inbox). |
| `agent_tools.py` | The tools the agent may call: validated inputs, trimmed outputs, audit channel. |
| `mcp_server.py` | The same tools as an MCP server (stdio). |
| `seed_data/*.csv` | The data (48 employees, usage, requests, 2026 holidays). Edit these by hand if needed. |
| `seed_data/generate_seed_data.py` | One-off generator that produced `employees.csv` and `leave_usage.csv`. Re-running overwrites them. |
| `tests/` | pytest tests (rules, seed integrity, access rules, audit). |

## Design rules

- **Two organisations with their real rules.** Each employee has `policy_org` = `DEF` or `BLDEDU`
  (DOC-008 and DOC-009 in the eval corpus). The balances agree with the gold answers in the eval set.
- **The actor is never taken from the model.** Every function's first argument is `actor_id`,
  supplied by the app session. Employees see their own records, managers their reporting
  chain (limited fields), HR everyone. Anything else returns `not_allowed`, and the message is the
  same whether the record exists or not.
- **Structured results.** Functions return `{"ok": True, ...}` or `{"ok": False, "error": code, "message": text}`
  and never raise to the caller.
- **Audit trail.** One `audit_log` row per call, including denied calls, tagged `app` or `agent`.
- **Schema version.** `db.SCHEMA_VERSION` is stored in the DB file. A DB built with an older version is rebuilt
  from the seed files the next time it is opened, so teammates never need to reset by hand after a schema change.
- **Fixed "today".** `HRMS_TODAY` (default `2026-11-16`) drives accrual and, later, notice and
  back-dating rules, so demos and tests are repeatable. Balances are computed at seed time,
  so rebuild the DB after changing it.

## Demo personas

| ID | Who | Good for |
|---|---|---|
| D010 | DEF, joined 18 Mar 2026 | Q001: no credit for the joining month (earned leave starts in April) |
| D011 | DEF, joined 12 Mar 2026 | Full credit for the joining month |
| D012 / D013 | DEF, 16 / 26 months of service | Q029: work-from-home needs 2 years |
| D014 | DEF probation | No earned leave |
| D015 / B018 | DEF / BLDEDU, serving notice | No leave can be applied for during notice |
| D016 / D017 | DEF women with 1 / 2 children | Maternity leave eligible / not eligible |
| D019 | DEF, 148 days earned leave carried forward | The 150-day cap |
| D020 | DEF, casual leave fully used | Zero balance, insufficient-balance errors |
| D003 / D002 | DEF manager / director | Manager views, indirect reports |
| D001 | DEF HR manager | HR-wide access |
| B010 | BLDEDU non-teaching, joined 18 Mar 2026 | Q007: half credit for the joining month |
| B014 / B015 | BLDEDU non-teaching / teaching | Q048: earned leave 30 vs 10 days |
| B017 | BLDEDU, commuted leave used | Debited at 2x |
| B003 / B002 | BLDEDU HOD / HR officer | Manager and HR views |

## Known data caveats (UNVERIFIED)

- **2026 holiday dates are approximate.** Dates for movable festivals (Holi, Eid, Diwali, etc.) were written
  from memory and must be checked before being quoted as real. The *counts and types* follow the policies
  (DEF: 10 national + restricted; BLDEDU: 3 compulsory + 12 public).
- **BLDEDU contradicts itself** in three places. The values used are noted in `rules.py` (`note`) :
  earned leave 10 vs 30 (section values used), casual leave 15 vs "7 per half-year" (table value used),
  commuted leave 20 per completed year vs 10 (table value used).
- **Historical usage is a number, not requests.** Leave taken before the system's start is stored as `used`
  in the balance; only the 8 requests in `leave_requests.csv` exist as individual records.
- **Simplifications in the checks (UNVERIFIED readings):** back-dating is measured from the start date;
  restricted holidays count as working days; the sandwich rule is applied to weekly offs only (not to holidays);
  a sick-leave certificate is required once 2 or more sick days are used this year; BLDEDU maternity
  "up to two existing children" is read as fewer than 2; an `APPROVED` request counts as `used` even before
  its dates; a request must fall inside the current calendar year.
- **Not modelled:** Leave Without Pay (the BLDEDU error message only points to it), loss of pay during notice
  (leave is simply refused), partial cancellation, comp-off validity windows, abortion / adoption / special
  disability leave, study and duty leave (BLDEDU gives conflicting limits).
- AI assistance: this component was written with Claude Code. Disclose tool, purpose and extent in the report
  (Student Handbook 6.2).

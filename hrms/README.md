# Dummy HRMS / leave management

A small, **fully synthetic** HR system that the HR Policy Navigator agent calls as tools
(leave balance, holidays, manager lookup, and later apply/cancel leave). It exists so the
agent can combine *policy* (from the RAG corpus) with *employee facts* (from a database),
as the mentor asked on 14 Nov. No real person or company data is used.

Status: **Phases 1 and 2 done** (schema, rules, seed data, read functions, request validation,
draft / submit / cancel / modify, tests). Still to come: manager approval, API and Streamlit views,
agent tool wrappers, MCP server, eval items.

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

```
DRAFT --submit--> PENDING --approve--> APPROVED        (approve / reject / send back: manager step, next phase)
  |                  |  \--reject--> REJECTED
  |                  \--send back--> SENT_BACK --submit--> PENDING
  \--cancel--> CANCELLED    (PENDING and APPROVED can be cancelled too)
```

Checks applied: leave type open to the employee, valid dates within this year, no leave during notice,
half-day rules, minimum and maximum days, advance notice, back-dating limit, no advance sick leave,
balance (commuted leave counts double), no overlap with another active request, DEF sandwich rule,
holidays and weekly offs not counted (maternity counts calendar days), supporting documents.

## Folder map

| File | Purpose |
|---|---|
| `rules.py` | Leave rules for both organisations. **Single source of truth**, each rule cites its policy section. |
| `schema.sql` | Tables: employees, leave_types, leave_balances, leave_requests, holidays, audit_log. |
| `db.py` | Connection helper, DB path, the fixed "today". |
| `seed.py` | Builds the DB from `schema.sql`, `rules.py` and the CSVs. |
| `service.py` | Functions the tools call. Every call checks who is asking and writes an audit row. |
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
- **Audit trail.** One `audit_log` row per call, including denied calls.
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

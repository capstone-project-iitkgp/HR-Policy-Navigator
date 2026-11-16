# Dummy HRMS / leave management

A small, **fully synthetic** HR system that the HR Policy Navigator agent calls as tools
(leave balance, holidays, manager lookup, and later apply/cancel leave). It exists so the
agent can combine *policy* (from the RAG corpus) with *employee facts* (from a database),
as the mentor asked on 14 Nov. No real person or company data is used.

Status: **Phase 1 done** (schema, rules, seed data, read functions, tests).
Phases 2-5 (apply-leave lifecycle, manager approval, API/UI, MCP server, eval items) are in the plan.

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
| D015 | DEF, serving notice | Leave during notice becomes loss of pay (rule enforced in Phase 2) |
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
- **Not modelled yet:** sandwich rule, notice-period loss of pay, working-day counting, probation timing
  for DEF comp-off, study/duty leave (BLDEDU). Study leave is left out because the policy gives conflicting limits.
- AI assistance: this component was written with Claude Code. Disclose tool, purpose and extent in the report
  (Student Handbook 6.2).

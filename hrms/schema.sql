-- Dummy HRMS schema (SQLite). All data is synthetic. See hrms/README.md.

CREATE TABLE employees (
    emp_id        TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL,
    policy_org    TEXT NOT NULL CHECK (policy_org IN ('DEF', 'BLDEDU')),
    category      TEXT NOT NULL,   -- see rules.CATEGORIES
    grade         TEXT,
    designation   TEXT,
    department    TEXT,
    manager_id    TEXT REFERENCES employees(emp_id),
    join_date     TEXT NOT NULL,   -- ISO date
    gender        TEXT CHECK (gender IN ('F', 'M', 'O')),
    num_children  INTEGER NOT NULL DEFAULT 0,
    work_week_days REAL NOT NULL DEFAULT 5,  -- 5, 5.5 or 6
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'notice')),
    notice_start  TEXT,
    role          TEXT NOT NULL DEFAULT 'employee' CHECK (role IN ('employee', 'manager', 'hr'))
);

-- Rule values live in rules.py (single source of truth); this table is a
-- copy written at seed time so SQL queries and the UI can read them.
CREATE TABLE leave_types (
    org            TEXT NOT NULL,
    code           TEXT NOT NULL,
    name           TEXT NOT NULL,
    annual_days    REAL NOT NULL,
    credit         TEXT NOT NULL,   -- monthly | half_yearly | event | earned
    carry_cap      REAL NOT NULL,
    max_stretch    REAL,
    min_notice     INTEGER NOT NULL,
    max_backdate   INTEGER NOT NULL,
    doc_rule       TEXT,
    pay_factor     REAL NOT NULL DEFAULT 1,   -- days debited per day taken
    source_ref     TEXT NOT NULL,
    PRIMARY KEY (org, code)
);

CREATE TABLE leave_balances (
    emp_id          TEXT NOT NULL REFERENCES employees(emp_id),
    leave_code      TEXT NOT NULL,
    year            INTEGER NOT NULL,
    entitled        REAL NOT NULL,   -- credited so far this year, as of HRMS_TODAY at seed time
    carried_forward REAL NOT NULL DEFAULT 0,
    used            REAL NOT NULL DEFAULT 0,   -- approved and taken
    pending         REAL NOT NULL DEFAULT 0,   -- in PENDING requests
    PRIMARY KEY (emp_id, leave_code, year)
);

CREATE TABLE leave_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id        TEXT NOT NULL REFERENCES employees(emp_id),
    leave_code    TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    end_date      TEXT NOT NULL,
    half_day      INTEGER NOT NULL DEFAULT 0,
    days          REAL NOT NULL,
    reason        TEXT,
    status        TEXT NOT NULL CHECK (status IN
                  ('DRAFT', 'PENDING', 'APPROVED', 'REJECTED', 'SENT_BACK', 'CANCELLED')),
    created_on    TEXT NOT NULL,
    applied_on    TEXT,
    decided_by    TEXT,
    decided_on    TEXT,
    decision_note TEXT,
    doc_attached  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE holidays (
    org   TEXT NOT NULL,
    date  TEXT NOT NULL,
    name  TEXT NOT NULL,
    type  TEXT NOT NULL CHECK (type IN ('national', 'restricted', 'public', 'compulsory')),
    PRIMARY KEY (org, date, name)
);

CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    role        TEXT,
    action      TEXT NOT NULL,
    channel     TEXT NOT NULL DEFAULT 'app',   -- 'app' (screens, API) or 'agent' (agent tools)
    request_id  INTEGER,
    ok          INTEGER NOT NULL,
    details     TEXT
);

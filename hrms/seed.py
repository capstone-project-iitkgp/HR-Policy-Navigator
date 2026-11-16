"""Build the dummy HRMS database from schema.sql, rules.py and seed_data/*.csv.

    python -m hrms.seed        (run from the Code/ folder; rebuilds a clean DB)

The DB is also built automatically the first time db.get_conn() finds no file.
"""

import csv
import sqlite3
import sys
from pathlib import Path

from . import db, rules

HERE = Path(__file__).resolve().parent
SEED_DIR = HERE / "seed_data"


def _read(name):
    with open(SEED_DIR / name, newline="") as f:
        return list(csv.DictReader(f))


def _blank(value):
    return value if value != "" else None


def build(path=None) -> dict:
    """Delete and rebuild the DB. Returns row counts."""
    path = Path(path or db.db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    today = db.today()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript((HERE / "schema.sql").read_text())
    conn.execute("PRAGMA user_version = %d" % db.SCHEMA_VERSION)

    # employees (managers appear after their reports, so check foreign keys at the end)
    employees = _read("employees.csv")
    for e in employees:
        e["num_children"] = int(e["num_children"])
        conn.execute(
            "INSERT INTO employees VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e["emp_id"], e["name"], e["email"], e["policy_org"], e["category"],
             _blank(e["grade"]), _blank(e["designation"]), _blank(e["department"]),
             _blank(e["manager_id"]), e["join_date"], _blank(e["gender"]),
             int(e["num_children"]), float(e["work_week_days"]), e["status"],
             _blank(e["notice_start"]), e["role"]))

    # leave types (copy of rules.py; per-category values show the maximum)
    for org, leaves in rules.LEAVES.items():
        for code, r in leaves.items():
            annual = r["annual"]
            annual = max(annual.values()) if isinstance(annual, dict) else annual
            stretch = r["max_stretch"]
            stretch = max(stretch.values()) if isinstance(stretch, dict) else stretch
            ref = r["ref"] + (" | " + r["note"] if r.get("note") else "")
            conn.execute(
                "INSERT INTO leave_types VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (org, code, r["name"], annual, r["credit"], r["carry_cap"], stretch,
                 r["min_notice"], r["max_backdate"], r["doc_rule"], r["pay_factor"], ref))

    # balances: entitlement from rules, usage and carry-forward from the CSV
    usage = {(u["emp_id"], u["leave_code"]): u for u in _read("leave_usage.csv")}
    for e in employees:
        for code in rules.leave_codes(e["policy_org"]):
            if not rules.eligible(e, code)[0]:
                continue
            u = usage.get((e["emp_id"], code))
            if rules.LEAVES[e["policy_org"]][code]["credit"] == "earned":
                if u is None:
                    continue  # comp-off exists only once something has been earned
                entitled = float(u["earned"])
            else:
                entitled = rules.accrued_to_date(e, code, today)
            conn.execute(
                "INSERT INTO leave_balances VALUES (?,?,?,?,?,?,?)",
                (e["emp_id"], code, today.year, entitled,
                 float(u["carried_forward"]) if u else 0.0,
                 float(u["used"]) if u else 0.0, 0.0))

    # leave requests; approved ones add to "used", pending ones to "pending"
    org_of = {e["emp_id"]: e["policy_org"] for e in employees}
    for r in _read("leave_requests.csv"):
        conn.execute(
            "INSERT INTO leave_requests (emp_id, leave_code, start_date, end_date, half_day,"
            " days, reason, status, created_on, applied_on, decided_by, decided_on,"
            " decision_note, doc_attached) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["emp_id"], r["leave_code"], r["start_date"], r["end_date"], int(r["half_day"]),
             float(r["days"]), _blank(r["reason"]), r["status"], r["created_on"],
             _blank(r["applied_on"]), _blank(r["decided_by"]), _blank(r["decided_on"]),
             _blank(r["decision_note"]), int(r["doc_attached"])))
        column = {"APPROVED": "used", "PENDING": "pending"}.get(r["status"])
        if column:
            factor = rules.LEAVES[org_of[r["emp_id"]]][r["leave_code"]]["pay_factor"]
            cur = conn.execute(
                "UPDATE leave_balances SET %s = %s + ? WHERE emp_id=? AND leave_code=? AND year=?"
                % (column, column), (float(r["days"]) * factor, r["emp_id"], r["leave_code"], today.year))
            if cur.rowcount != 1:
                raise ValueError("seed request has no balance row: %s" % r)

    for h in _read("holidays_2026.csv"):
        conn.execute("INSERT INTO holidays VALUES (?,?,?,?)", (h["org"], h["date"], h["name"], h["type"]))

    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise ValueError("seed data has broken foreign keys: %s" % [tuple(b) for b in bad])
    conn.commit()
    counts = {t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
              for t in ("employees", "leave_types", "leave_balances", "leave_requests", "holidays")}
    conn.close()
    return counts


if __name__ == "__main__":
    result = build()
    print("Built %s" % db.db_path())
    for table, n in result.items():
        print("  %-15s %d" % (table, n))
    sys.exit(0)

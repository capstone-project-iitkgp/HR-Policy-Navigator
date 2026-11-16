"""HRMS service: plain functions the agent tools, the API and the MCP server all call.

Rules for every function here:
  * the first argument is `actor_id`, the logged-in employee. It comes from the
    app session, never from the language model.
  * the result is a dict: {"ok": True, ...} or {"ok": False, "error": code, "message": text}.
    Nothing is raised to the caller, so a tool always returns something structured.
  * every call writes one row to audit_log, successful or not.
"""

import json
from contextlib import closing
from datetime import datetime

from . import db, rules

# What a manager may see about a report. HR and the employee themselves see everything.
LIMITED_FIELDS = ["emp_id", "name", "email", "designation", "department", "manager_id", "join_date"]


# --- small helpers -------------------------------------------------------
def _ok(**fields):
    result = {"ok": True}
    result.update(fields)
    return result


def _err(code, message):
    return {"ok": False, "error": code, "message": message}


def _one(conn, sql, args=()):
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None


def _audit(conn, actor, action, result, details):
    conn.execute(
        "INSERT INTO audit_log (ts, actor, role, action, request_id, ok, details) VALUES (?,?,?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), actor["emp_id"] if actor else "unknown",
         actor["role"] if actor else None, action, result.get("request_id"),
         1 if result["ok"] else 0,
         json.dumps(dict(details, error=result.get("error")), default=str)))


def _run(action, actor_id, details, body):
    """Look up the actor, run body(conn, actor), write the audit row, return the result."""
    with closing(db.get_conn()) as conn:
        actor = _one(conn, "SELECT * FROM employees WHERE emp_id = ?", (actor_id,))
        if actor is None:
            result = _err("unknown_actor", "Unknown user '%s'." % actor_id)
        else:
            result = body(conn, actor)
        _audit(conn, actor, action, result, details)
        conn.commit()
        return result


def _can_view(conn, actor, target_id):
    """Self and HR see anyone; a manager sees everyone below them in the reporting chain."""
    if actor["emp_id"] == target_id or actor["role"] == "hr":
        return True
    current = _one(conn, "SELECT manager_id FROM employees WHERE emp_id = ?", (target_id,))
    for _ in range(10):  # reporting chains are short; the limit also stops cycles
        if not current or not current["manager_id"]:
            return False
        if current["manager_id"] == actor["emp_id"]:
            return True
        current = _one(conn, "SELECT manager_id FROM employees WHERE emp_id = ?", (current["manager_id"],))
    return False


def _target(conn, actor, emp_id):
    """Return (target_row, None) or (None, error_result), applying the access rule."""
    target_id = emp_id or actor["emp_id"]
    target = _one(conn, "SELECT * FROM employees WHERE emp_id = ?", (target_id,))
    if target is None or not _can_view(conn, actor, target_id):
        # same message for "does not exist" and "not allowed", so IDs cannot be probed
        return None, _err("not_allowed", "You can only view your own records, or your team's if you are a manager.")
    return target, None


# --- read functions ------------------------------------------------------
def get_employee(actor_id, emp_id=None):
    """Profile of the actor (default) or of someone they are allowed to see."""
    def body(conn, actor):
        target, error = _target(conn, actor, emp_id)
        if error:
            return error
        if actor["emp_id"] == target["emp_id"] or actor["role"] == "hr":
            return _ok(employee=target)
        return _ok(employee={k: target[k] for k in LIMITED_FIELDS})

    return _run("get_employee", actor_id, {"emp_id": emp_id}, body)


def get_leave_balance(actor_id, emp_id=None, leave_code=None):
    """Leave balances for this year. `available` = entitled + carried_forward - used - pending."""
    def body(conn, actor):
        target, error = _target(conn, actor, emp_id)
        if error:
            return error
        org = target["policy_org"]
        year = db.today().year
        sql = ("SELECT b.*, t.name AS leave_name, t.source_ref FROM leave_balances b "
               "JOIN leave_types t ON t.org = ? AND t.code = b.leave_code "
               "WHERE b.emp_id = ? AND b.year = ?")
        args = [org, target["emp_id"], year]
        if leave_code:
            sql += " AND b.leave_code = ?"
            args.append(leave_code.upper())
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY b.leave_code", args)]
        if leave_code and not rows:
            _, reason = rules.eligible(target, leave_code.upper())
            return _err("not_entitled", reason or "No %s balance found." % leave_code)
        for r in rows:
            r["available"] = round(r["entitled"] + r["carried_forward"] - r["used"] - r["pending"], 2)
        return _ok(emp_id=target["emp_id"], policy_org=org, year=year,
                   as_of=db.today().isoformat(), balances=rows)

    return _run("get_leave_balance", actor_id, {"emp_id": emp_id, "leave_code": leave_code}, body)


def get_holidays(actor_id, holiday_type=None):
    """The holiday calendar of the actor's own organisation, for the current year."""
    def body(conn, actor):
        sql = "SELECT date, name, type FROM holidays WHERE org = ? AND date LIKE ?"
        args = [actor["policy_org"], "%d-%%" % db.today().year]
        if holiday_type:
            sql += " AND type = ?"
            args.append(holiday_type)
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY date", args)]
        note = None
        if actor["policy_org"] == "DEF":
            note = "Employees may take any 2 restricted holidays a year (DEF HR Manual 5.9)."
        return _ok(policy_org=actor["policy_org"], holidays=rows, note=note)

    return _run("get_holidays", actor_id, {"holiday_type": holiday_type}, body)


def get_manager(actor_id, emp_id=None):
    """Reporting manager of the actor (default) or of someone they are allowed to see."""
    def body(conn, actor):
        target, error = _target(conn, actor, emp_id)
        if error:
            return error
        if not target["manager_id"]:
            return _ok(emp_id=target["emp_id"], manager=None,
                       message="%s is at the top of the reporting line." % target["name"])
        manager = _one(conn, "SELECT emp_id, name, email, designation, department "
                             "FROM employees WHERE emp_id = ?", (target["manager_id"],))
        return _ok(emp_id=target["emp_id"], manager=manager)

    return _run("get_manager", actor_id, {"emp_id": emp_id}, body)


def list_requests(actor_id, emp_id=None, status=None):
    """Leave requests of the actor (default) or of someone they are allowed to see."""
    def body(conn, actor):
        target, error = _target(conn, actor, emp_id)
        if error:
            return error
        sql = "SELECT * FROM leave_requests WHERE emp_id = ?"
        args = [target["emp_id"]]
        if status:
            sql += " AND status = ?"
            args.append(status.upper())
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY start_date DESC, id DESC", args)]
        return _ok(emp_id=target["emp_id"], requests=rows)

    return _run("list_requests", actor_id, {"emp_id": emp_id, "status": status}, body)

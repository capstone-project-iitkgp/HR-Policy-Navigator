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
from datetime import date, datetime

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
         actor["role"] if actor else None, action,
         result.get("request_id") or details.get("request_id"),
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


# --- applying for leave --------------------------------------------------
# Request life cycle (who may move a request, and what happens to the balance):
#
#   DRAFT --submit (employee confirms)--> PENDING --approve (manager)--> APPROVED
#     |                                      |  \--reject---------------> REJECTED
#     |                                      \----send back-----------> SENT_BACK --submit--> PENDING
#     \--cancel--> CANCELLED   (PENDING and APPROVED can be cancelled too, APPROVED only until its end date)
#
# Only the employee's own confirmation (`confirmed=True`) moves a request out of DRAFT,
# and the agent tools never pass it, so the agent can prepare a request but never file one.
# Balance: PENDING days sit in `pending`, APPROVED days in `used`, anything else holds nothing.
# Approve / reject / send back are in decide_request at the bottom of this file.
BLOCKING = ("DRAFT", "PENDING", "APPROVED", "SENT_BACK")  # statuses that occupy their dates


def _iso(value):
    """Parse YYYY-MM-DD, or return None."""
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _approver(conn, emp):
    """Who decides this employee's leave: the reporting manager, else HR of the same organisation."""
    row = None
    if emp["manager_id"]:
        row = _one(conn, "SELECT emp_id, name, designation FROM employees WHERE emp_id = ?",
                   (emp["manager_id"],))
    if row is None:
        row = _one(conn, "SELECT emp_id, name, designation FROM employees "
                         "WHERE role = 'hr' AND policy_org = ? ORDER BY emp_id", (emp["policy_org"],))
    return row


def _check(conn, emp, leave_code, start_date, end_date, half_day, exclude_id=None):
    """Test a leave request against the policy. Returns a dict with `valid`, `errors`, `warnings`
    and the numbers behind them. `exclude_id` is a request being edited, so it does not clash with itself."""
    today = db.today()
    leave = str(leave_code or "").upper()
    errors, warnings = [], []

    def fail(kind, message):
        errors.append({"code": kind, "message": message})

    def result(**extra):
        out = dict(valid=not errors, errors=errors, warnings=warnings, leave_code=leave,
                   start_date=str(start_date), end_date=str(end_date), half_day=bool(half_day))
        out.update(extra)
        return out

    ok, reason = rules.eligible(emp, leave)
    if not ok:
        fail("not_eligible", reason)
        return result()
    rule = rules.LEAVES[emp["policy_org"]][leave]
    start, end = _iso(start_date), _iso(end_date)
    if start is None or end is None:
        fail("bad_date", "Dates must be written as YYYY-MM-DD.")
        return result()
    if end < start:
        fail("bad_date", "The end date is before the start date.")
        return result()
    if start.year != today.year or end.year != today.year:
        fail("wrong_year", "Leave balances are kept per calendar year, so apply for dates within %d." % today.year)
        return result()
    if emp["status"] == "notice":
        fail("on_notice", "No leave can be applied for or granted during the notice period "
                          "(DEF HR Manual 5.6; BLDEDU HR Policy 5.G).")
        return result()
    if half_day:
        if not rule.get("half_day"):
            fail("half_day_not_allowed", "%s cannot be taken as a half day." % rule["name"])
            return result()
        if start != end:
            fail("half_day_range", "A half day can only be a single day.")
            return result()

    holidays = {date.fromisoformat(r["date"]) for r in conn.execute(
        "SELECT date FROM holidays WHERE org = ? AND type IN ('national', 'public', 'compulsory')",
        (emp["policy_org"],))}
    counted = rules.count_leave_days(emp, leave, start, end, bool(half_day), holidays)
    days = counted["days"]
    if days == 0:
        fail("no_working_days", "Every date falls on a weekly off or a holiday, so there is no leave to apply for.")
        return result(days=0.0, skipped=counted["skipped"])

    lowest, longest = rules.min_days(emp, leave), rules.max_stretch(emp, leave)
    if days < lowest:
        fail("below_minimum", "The minimum for %s is %g days; this request uses %g." % (rule["name"], lowest, days))
    if longest is not None and days > longest:
        fail("above_maximum", "The maximum for %s at a stretch is %g days; this request uses %g." % (
            rule["name"], longest, days))

    lead = (start - today).days  # days from today until the leave starts (negative = back-dated)
    if rule.get("no_advance") and lead > 0:
        fail("no_advance", "%s cannot be applied for in advance." % rule["name"])
    if lead < 0 and -lead > rule["max_backdate"]:
        if rule["max_backdate"] == 0:
            fail("backdated", "Back-dated applications are not allowed for %s." % rule["name"])
        else:
            fail("backdated", "%s must be applied for within %d days of the start date; this started %d days ago." % (
                rule["name"], rule["max_backdate"], -lead))
    if 0 <= lead < rule["min_notice"]:
        fail("short_notice", "%s must be applied for at least %d days in advance; this starts in %d days." % (
            rule["name"], rule["min_notice"], lead))

    bal = _one(conn, "SELECT * FROM leave_balances WHERE emp_id = ? AND leave_code = ? AND year = ?",
               (emp["emp_id"], leave, today.year))
    available = (bal["entitled"] + bal["carried_forward"] - bal["used"] - bal["pending"]) if bal else 0.0
    if exclude_id:  # the request being edited gives back what it was holding
        own = _one(conn, "SELECT status, days FROM leave_requests WHERE id = ?", (exclude_id,))
        if own and own["status"] in ("PENDING", "APPROVED"):
            available += own["days"] * rule["pay_factor"]
    debit = days * rule["pay_factor"]
    if debit > available + 1e-9:
        message = "You have %g days of %s available but this request needs %g." % (
            round(available, 2), rule["name"], debit)
        if emp["policy_org"] == "BLDEDU":
            message += " Leave Without Pay can be requested instead (BLDEDU 5.G.7); this system does not handle it."
        fail("insufficient_balance", message)

    clash = _one(conn, "SELECT id, leave_code, start_date, end_date, status FROM leave_requests "
                       "WHERE emp_id = ? AND status IN (%s) AND start_date <= ? AND end_date >= ? AND id != ? LIMIT 1"
                       % ",".join("?" * len(BLOCKING)),
                 [emp["emp_id"]] + list(BLOCKING) + [end.isoformat(), start.isoformat(), exclude_id or 0])
    if clash:
        fail("overlap", "These dates overlap request #%d (%s, %s to %s, %s)." % (
            clash["id"], clash["leave_code"], clash["start_date"], clash["end_date"], clash["status"]))

    if counted["sandwiched"]:
        warnings.append({"code": "sandwich", "message": (
            "You are away on a working Friday and the Monday after, so Saturday and Sunday also count "
            "as leave (+%d days). DEF HR Manual 5.4.1." % len(counted["sandwiched"]))})
    if emp["policy_org"] == "DEF" and days > 7 and lead < 15:
        warnings.append({"code": "long_leave", "message": (
            "Leave of more than 7 days should be planned with your manager at least 15 days ahead (DEF 5.4.1).")})
    if emp["policy_org"] == "BLDEDU" and days >= 5:
        warnings.append({"code": "long_leave", "message": (
            "Leave of 5 or more continuous working days should be planned well in advance (BLDEDU 5.G).")})
    if rule["pay_factor"] != 1:
        warnings.append({"code": "debit_factor", "message": (
            "%s is debited at %g times the days taken." % (rule["name"], rule["pay_factor"]))})

    needs_doc, doc_note = rules.needs_document(emp, leave, bal["used"] if bal else 0)
    return result(
        leave_name=rule["name"], days=days, debit=debit, balance_before=round(available, 2),
        balance_after=round(available - debit, 2), needs_doc=needs_doc, doc_note=doc_note,
        approver=_approver(conn, emp), skipped=counted["skipped"],
        sandwiched=[d.isoformat() for d in counted["sandwiched"]], rule_refs=[rule["ref"]])


def _own_request(conn, actor, request_id):
    """The actor's own request, or an error. A missing request and someone else's look the same."""
    req = _one(conn, "SELECT * FROM leave_requests WHERE id = ? AND emp_id = ?", (request_id, actor["emp_id"]))
    if req is None:
        return None, _err("not_found", "No leave request #%s found for you." % request_id)
    return req, None


def _reserved(status, amount):
    """(used, pending) days a request holds in its balance, given its status."""
    if status == "APPROVED":
        return amount, 0.0
    if status == "PENDING":
        return 0.0, amount
    return 0.0, 0.0


def _rebook(conn, org, req, new_status, new_days):
    """Update the balance for a request moving to `new_status` with `new_days` days."""
    factor = rules.LEAVES[org][req["leave_code"]]["pay_factor"]
    old_used, old_pending = _reserved(req["status"], req["days"] * factor)
    new_used, new_pending = _reserved(new_status, new_days * factor)
    conn.execute(
        "UPDATE leave_balances SET used = ROUND(used + ?, 2), pending = ROUND(pending + ?, 2) "
        "WHERE emp_id = ? AND leave_code = ? AND year = ?",
        (new_used - old_used, new_pending - old_pending, req["emp_id"], req["leave_code"],
         int(req["start_date"][:4])))


def _invalid(check):
    out = _err("invalid_request", "The request breaks the leave policy: " + " ".join(
        e["message"] for e in check["errors"]))
    out["errors"] = check["errors"]
    return out


def _needs_confirmation(message, preview):
    out = _err("confirmation_required", message)
    out["preview"] = preview
    return out


def check_leave_request(actor_id, leave_code, start_date, end_date, half_day=False):
    """Test a request for the actor without saving anything. `valid` says whether it would be accepted."""
    def body(conn, actor):
        return _ok(**_check(conn, actor, leave_code, start_date, end_date, half_day))

    return _run("check_leave_request", actor_id, {
        "leave_code": leave_code, "start_date": start_date, "end_date": end_date, "half_day": half_day}, body)


def create_leave_draft(actor_id, leave_code, start_date, end_date, half_day=False, reason=None):
    """Save a DRAFT request for the actor if it passes the checks. Nothing is filed yet."""
    def body(conn, actor):
        check = _check(conn, actor, leave_code, start_date, end_date, half_day)
        if not check["valid"]:
            return _invalid(check)
        cur = conn.execute(
            "INSERT INTO leave_requests (emp_id, leave_code, start_date, end_date, half_day, days, reason,"
            " status, created_on) VALUES (?,?,?,?,?,?,?,?,?)",
            (actor["emp_id"], check["leave_code"], start_date, end_date, int(bool(half_day)),
             check["days"], reason, "DRAFT", db.today().isoformat()))
        return _ok(request_id=cur.lastrowid, status="DRAFT", check=check,
                   message="Draft saved. It is not filed until the employee confirms.")

    return _run("create_leave_draft", actor_id, {
        "leave_code": leave_code, "start_date": start_date, "end_date": end_date,
        "half_day": half_day}, body)


def submit_request(actor_id, request_id, confirmed=False, doc_attached=False):
    """File a DRAFT (or SENT_BACK) request with the approver. Needs `confirmed=True`, which only
    the employee's own confirmation in the app sets, and the document when one is required."""
    def body(conn, actor):
        req, error = _own_request(conn, actor, request_id)
        if error:
            return error
        if req["status"] not in ("DRAFT", "SENT_BACK"):
            return _err("invalid_state", "Only a draft or a request sent back can be submitted; this one is %s." % req["status"])
        check = _check(conn, actor, req["leave_code"], req["start_date"], req["end_date"],
                       req["half_day"], exclude_id=req["id"])
        if not check["valid"]:
            return _invalid(check)
        if not confirmed:
            return _needs_confirmation("The employee must confirm before this request is filed.", check)
        if check["needs_doc"] and not doc_attached:
            return _err("document_required", check["doc_note"])
        _rebook(conn, actor["policy_org"], req, "PENDING", check["days"])
        conn.execute("UPDATE leave_requests SET status = 'PENDING', days = ?, applied_on = ?, doc_attached = ?,"
                     " decided_by = NULL, decided_on = NULL, decision_note = NULL WHERE id = ?",
                     (check["days"], db.today().isoformat(), int(bool(doc_attached)), req["id"]))
        return _ok(request_id=req["id"], status="PENDING", days=check["days"], approver=check["approver"])

    return _run("submit_request", actor_id, {
        "request_id": request_id, "confirmed": confirmed, "doc_attached": doc_attached}, body)


def cancel_request(actor_id, request_id, confirmed=False):
    """Cancel the actor's own request. A draft or sent-back request cancels freely; a pending or
    approved one needs `confirmed=True`, and an approved one only until its end date (DEF 5.7)."""
    def body(conn, actor):
        req, error = _own_request(conn, actor, request_id)
        if error:
            return error
        if req["status"] not in BLOCKING:
            return _err("invalid_state", "A %s request cannot be cancelled." % req["status"])
        if req["status"] == "APPROVED" and db.today().isoformat() > req["end_date"]:
            return _err("too_late", "Approved leave can only be cancelled until its end date (%s)." % req["end_date"])
        if req["status"] in ("PENDING", "APPROVED") and not confirmed:
            return _needs_confirmation("The employee must confirm before a %s request is cancelled." % req["status"].lower(), req)
        _rebook(conn, actor["policy_org"], req, "CANCELLED", req["days"])
        conn.execute("UPDATE leave_requests SET status = 'CANCELLED', decided_by = ?, decided_on = ?,"
                     " decision_note = 'Cancelled by employee' WHERE id = ?",
                     (actor["emp_id"], db.today().isoformat(), req["id"]))
        return _ok(request_id=req["id"], status="CANCELLED", was=req["status"])

    return _run("cancel_request", actor_id, {"request_id": request_id, "confirmed": confirmed}, body)


def modify_request(actor_id, request_id, start_date, end_date, half_day=None, confirmed=False):
    """Change the dates of the actor's own request and check them again. Changing an APPROVED request
    sends it back to PENDING for a new decision, so it needs `confirmed=True`; at DEF the new start
    date must also lie within the old dates (DEF 5.8)."""
    def body(conn, actor):
        req, error = _own_request(conn, actor, request_id)
        if error:
            return error
        if req["status"] not in BLOCKING:
            return _err("invalid_state", "A %s request cannot be changed." % req["status"])
        half = bool(req["half_day"]) if half_day is None else bool(half_day)
        if (start_date, end_date, half) == (req["start_date"], req["end_date"], bool(req["half_day"])):
            return _err("no_change", "The dates are the same as before.")
        if (req["status"] == "APPROVED" and actor["policy_org"] == "DEF"
                and not req["start_date"] <= str(start_date) <= req["end_date"]):
            return _err("modify_rule", "The new start date must lie between the old start and end dates (DEF HR Manual 5.8).")
        check = _check(conn, actor, req["leave_code"], start_date, end_date, half, exclude_id=req["id"])
        if not check["valid"]:
            return _invalid(check)
        if req["status"] == "APPROVED" and not confirmed:
            return _needs_confirmation("Changing approved leave needs a new approval, so the employee must confirm.", check)
        new_status = "PENDING" if req["status"] == "APPROVED" else req["status"]
        _rebook(conn, actor["policy_org"], req, new_status, check["days"])
        conn.execute("UPDATE leave_requests SET start_date = ?, end_date = ?, half_day = ?, days = ?, status = ?,"
                     " applied_on = CASE WHEN ? = 'PENDING' THEN ? ELSE applied_on END,"
                     " decided_by = CASE WHEN ? = 'PENDING' THEN NULL ELSE decided_by END,"
                     " decided_on = CASE WHEN ? = 'PENDING' THEN NULL ELSE decided_on END WHERE id = ?",
                     (start_date, end_date, int(half), check["days"], new_status,
                      new_status, db.today().isoformat(), new_status, new_status, req["id"]))
        return _ok(request_id=req["id"], status=new_status, days=check["days"], check=check)

    return _run("modify_request", actor_id, {
        "request_id": request_id, "start_date": start_date, "end_date": end_date, "confirmed": confirmed}, body)


# --- manager decisions ---------------------------------------------------
# Only the approver decides: the employee's reporting manager, or HR for someone with no manager
# (see _approver). Nobody decides their own request, and a skip-level manager cannot decide either.
# These functions are for the manager screens only; they are never given to the agent.
DECISIONS = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "SEND_BACK": "SENT_BACK"}


def list_pending_approvals(actor_id):
    """Pending requests waiting for the actor's decision, with the employee's balance for that leave type."""
    def body(conn, actor):
        rows = [dict(r) for r in conn.execute(
            "SELECT r.*, e.name AS employee_name, e.designation, e.department FROM leave_requests r "
            "JOIN employees e ON e.emp_id = r.emp_id "
            "WHERE r.status = 'PENDING' AND e.emp_id != ? "
            "AND (e.manager_id = ? OR (e.manager_id IS NULL AND e.policy_org = ? AND ? = 'hr')) "
            "ORDER BY r.start_date, r.id",
            (actor["emp_id"], actor["emp_id"], actor["policy_org"], actor["role"]))]
        for r in rows:
            r["balance"] = _one(
                conn, "SELECT entitled, carried_forward, used, pending FROM leave_balances "
                      "WHERE emp_id = ? AND leave_code = ? AND year = ?",
                (r["emp_id"], r["leave_code"], int(r["start_date"][:4])))
        return _ok(approvals=rows)

    return _run("list_pending_approvals", actor_id, {}, body)


def decide_request(actor_id, request_id, decision, note=None):
    """Approve, reject or send back a PENDING request. `decision` is APPROVE, REJECT or SEND_BACK;
    rejecting or sending back needs a note, which the employee sees."""
    def body(conn, actor):
        req = _one(conn, "SELECT * FROM leave_requests WHERE id = ?", (request_id,))
        owner = _one(conn, "SELECT * FROM employees WHERE emp_id = ?", (req["emp_id"],)) if req else None
        approver = _approver(conn, owner) if owner else None
        if approver is None or approver["emp_id"] != actor["emp_id"] or owner["emp_id"] == actor["emp_id"]:
            # same answer for "no such request" and "not yours to decide"
            return _err("not_found", "No leave request #%s found that you can decide." % request_id)
        new_status = DECISIONS.get(str(decision).upper())
        if new_status is None:
            return _err("bad_decision", "The decision must be APPROVE, REJECT or SEND_BACK.")
        if req["status"] != "PENDING":
            return _err("invalid_state", "Only a pending request can be decided; this one is %s." % req["status"])
        if new_status != "APPROVED" and not (note or "").strip():
            return _err("note_required", "Please give a reason when rejecting a request or sending it back.")
        _rebook(conn, owner["policy_org"], req, new_status, req["days"])
        conn.execute("UPDATE leave_requests SET status = ?, decided_by = ?, decided_on = ?, decision_note = ? WHERE id = ?",
                     (new_status, actor["emp_id"], db.today().isoformat(), (note or "").strip() or None, req["id"]))
        return _ok(request_id=req["id"], status=new_status, employee_id=owner["emp_id"])

    return _run("decide_request", actor_id, {"request_id": request_id, "decision": decision}, body)

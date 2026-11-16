"""The HRMS tools the agent may call, with validated inputs and compact, structured outputs.

    tool_schemas()                      -> OpenAI-style tool definitions to give the model
    run_tool(actor_id, name, arguments) -> a dict result, never an exception

What the agent can do, and what it cannot:
  * It acts as `actor_id`, the logged-in employee, chosen by the app. No tool has an employee-id
    argument, and any extra argument (emp_id, confirmed, ...) is rejected, so a model cannot
    switch whose data it reads or confirm anything on the employee's behalf.
  * It can read the employee's own data, check a request against the policy, save a DRAFT and
    cancel a draft. It has no tool to submit, change an approved request, approve or reject.
    Cancelling a request that is already filed answers `confirmation_required`; the employee
    does that in the app.
  * Every call is written to the audit log with channel "agent".

Results are trimmed on purpose: the model has an 8,192-token window shared by prompt and answer.
"""

import json
from collections import namedtuple
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import db, rules, service

Tool = namedtuple("Tool", "name description model call shape")

LEAVE_CODES = tuple(sorted({code for leaves in rules.LEAVES.values() for code in leaves}))
STATUSES = ("DRAFT", "PENDING", "APPROVED", "REJECTED", "SENT_BACK", "CANCELLED")
MAX_REQUESTS_SHOWN = 10


# --- inputs --------------------------------------------------------------
class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown arguments are an error, not ignored


def _upper(value):
    return value.strip().upper() if isinstance(value, str) else value


def _real_date(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError("must be a real date written YYYY-MM-DD, for example 2026-12-04")


class NoArgs(Args):
    pass


class BalanceArgs(Args):
    leave_code: Optional[Literal[LEAVE_CODES]] = Field(None, description="Leave type code; leave out for all types")
    _up = field_validator("leave_code", mode="before")(_upper)


class HolidayArgs(Args):
    holiday_type: Optional[Literal["national", "restricted", "public", "compulsory"]] = None


class RequestsArgs(Args):
    status: Optional[Literal[STATUSES]] = Field(None, description="Only requests with this status")
    _up = field_validator("status", mode="before")(_upper)


class LeaveArgs(Args):
    leave_code: Literal[LEAVE_CODES] = Field(description="Leave type code, for example CL, SL, EL")
    start_date: str = Field(description="First day of leave, YYYY-MM-DD")
    end_date: str = Field(description="Last day of leave, YYYY-MM-DD (same as start_date for one day)")
    half_day: bool = Field(False, description="True for a half day (one day only)")
    _up = field_validator("leave_code", mode="before")(_upper)
    _dates = field_validator("start_date", "end_date")(_real_date)


class DraftArgs(LeaveArgs):
    reason: Optional[str] = Field(None, max_length=200, description="Reason for the leave, if the user gave one")


class CancelArgs(Args):
    request_id: int = Field(description="Number of the leave request, from list_my_requests or create_leave_draft")


# --- outputs, trimmed to what the model needs ----------------------------
def _failure(result):
    """Keep the error code, the message and the list of rule errors; drop the rest."""
    out = {"ok": False, "error": result["error"], "message": result["message"]}
    if result.get("errors"):
        out["errors"] = result["errors"]
    return out


def _compact_check(c):
    out = {"valid": c["valid"], "errors": c["errors"], "warnings": [w["message"] for w in c["warnings"]]}
    if c.get("days") is not None:
        out.update(leave=c.get("leave_name"), days=c["days"], balance_before=c.get("balance_before"),
                   balance_after=c.get("balance_after"), needs_document=c.get("needs_doc"),
                   document_note=c.get("doc_note") or None, rule=c.get("rule_refs"))
        approver = c.get("approver")
        out["approver"] = "%s (%s)" % (approver["name"], approver["designation"]) if approver else None
    return out


def _profile(r):
    keep = ("name", "designation", "department", "policy_org", "category", "grade", "join_date",
            "gender", "num_children", "work_week_days", "status", "notice_start")
    return dict(ok=True, today=db.today().isoformat(), **{k: r["employee"][k] for k in keep})


def _balance(r):
    keep = ("leave_code", "leave_name", "entitled", "carried_forward", "used", "pending", "available")
    return {"ok": True, "as_of": r["as_of"], "policy_org": r["policy_org"],
            "balances": [{k: b[k] for k in keep} for b in r["balances"]]}


def _holidays(r):
    return {"ok": True, "policy_org": r["policy_org"], "note": r["note"], "holidays": r["holidays"]}


def _manager(r):
    m = r["manager"]
    return {"ok": True, "manager": {"name": m["name"], "designation": m["designation"]} if m else None}


def _requests(r):
    keep = ("id", "leave_code", "start_date", "end_date", "days", "status", "decision_note")
    shown = r["requests"][:MAX_REQUESTS_SHOWN]
    out = {"ok": True, "requests": [{k: q[k] for k in keep} for q in shown]}
    if len(r["requests"]) > len(shown):
        out["note"] = "Showing %d of %d requests." % (len(shown), len(r["requests"]))
    return out


def _check(r):
    return {"ok": True, **_compact_check(r)}


def _draft(r):
    return {"ok": True, "request_id": r["request_id"], "status": "DRAFT", "check": _compact_check(r["check"]),
            "next_step": "This is only a draft. The employee must open My requests in the HR app and press "
                         "Confirm and submit. You cannot submit it for them."}


def _cancel(r):
    return {"ok": True, "request_id": r["request_id"], "status": r["status"]}


def _shape(shaper, result):
    if not result["ok"]:
        out = _failure(result)
        if result["error"] == "confirmation_required":
            out["next_step"] = "Ask the employee to do this themselves in the HR app (My requests)."
        return out
    return shaper(result)


# --- the tools -----------------------------------------------------------
TOOLS = {t.name: t for t in [
    Tool("get_my_profile",
         "The employee's own HR profile (organisation, category, join date, status) and today's date.",
         NoArgs, lambda who, a: service.get_employee(who), _profile),
    Tool("get_leave_balance",
         "The employee's own leave balances this year (credited, used, pending, available). "
         "Use for 'how many leaves do I have left'. Do not ask for an employee id.",
         BalanceArgs, lambda who, a: service.get_leave_balance(who, None, a.leave_code), _balance),
    Tool("get_holidays",
         "The holiday calendar of the employee's own organisation for this year.",
         HolidayArgs, lambda who, a: service.get_holidays(who, a.holiday_type), _holidays),
    Tool("get_my_manager",
         "The employee's reporting manager (the person who approves their leave).",
         NoArgs, lambda who, a: service.get_manager(who), _manager),
    Tool("list_my_requests",
         "The employee's own leave requests with their status, newest first.",
         RequestsArgs, lambda who, a: service.list_requests(who, None, a.status), _requests),
    Tool("check_leave_request",
         "Test whether a leave request would be allowed, without saving it. Returns days used, "
         "balance after, warnings, whether a document is needed and who approves.",
         LeaveArgs, lambda who, a: service.check_leave_request(who, a.leave_code, a.start_date, a.end_date, a.half_day),
         _check),
    Tool("create_leave_draft",
         "Save a DRAFT leave request if it follows the policy. It is NOT submitted: the employee must "
         "confirm it in the HR app. Only call after the employee asks you to apply for leave.",
         DraftArgs, lambda who, a: service.create_leave_draft(
             who, a.leave_code, a.start_date, a.end_date, a.half_day, a.reason), _draft),
    Tool("cancel_leave_request",
         "Cancel one of the employee's own DRAFT requests. A request already submitted or approved "
         "cannot be cancelled by you; the employee does that in the HR app.",
         CancelArgs, lambda who, a: service.cancel_request(who, a.request_id), _cancel),
]}


def _strip_titles(node):
    """Drop pydantic's 'title' noise from a JSON schema to save tokens."""
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


def tool_schemas():
    """Tool definitions in the OpenAI function-calling format (the course endpoint is OpenAI-compatible)."""
    return [{"type": "function", "function": {
        "name": t.name, "description": t.description,
        "parameters": _strip_titles(t.model.model_json_schema())}} for t in TOOLS.values()]


# --- running a tool ------------------------------------------------------
def _bad(code, message, **extra):
    return dict({"ok": False, "error": code, "message": message}, **extra)


def run_tool(actor_id, name, arguments=None):
    """Validate the arguments, run the tool as `actor_id` and return a trimmed result dict.

    `arguments` may be a dict or the JSON string a model produces. Problems come back as
    {"ok": False, "error": ..., "message": ...} so the agent can correct itself and retry.
    """
    tool = TOOLS.get(name)
    if tool is None:
        return _bad("unknown_tool", "There is no tool called %r. Available tools: %s." % (name, ", ".join(TOOLS)))
    if arguments is None or arguments == "":
        args = {}
    elif isinstance(arguments, str):
        try:
            args = json.loads(arguments)
        except ValueError:
            return _bad("bad_arguments", "The arguments must be one JSON object, for example {\"leave_code\": \"CL\"}.")
    else:
        args = arguments
    if not isinstance(args, dict):
        return _bad("bad_arguments", "The arguments must be a JSON object, not a %s." % type(args).__name__)
    try:
        parsed = tool.model(**args)
    except ValidationError as e:
        problems = [{"argument": ".".join(str(p) for p in err["loc"]) or "(all)", "problem": err["msg"]}
                    for err in e.errors()]
        return _bad("bad_arguments", "The arguments for %s are not valid." % name, problems=problems)
    token = service.channel.set("agent")
    try:
        return _shape(tool.shape, tool.call(actor_id, parsed))
    except Exception:  # a tool must always answer; the details stay in the server log
        import logging
        logging.getLogger(__name__).exception("HRMS tool %s failed", name)
        return _bad("tool_failed", "The HR system could not complete this request. Try again later.")
    finally:
        service.channel.reset(token)

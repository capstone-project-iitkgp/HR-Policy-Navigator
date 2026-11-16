"""HTTP API for the dummy HRMS. A thin layer over hrms/service.py: no rules live here.

Who is acting: the `X-Employee-Id` header. This is a DEMO login; the header is trusted as is,
so replace it with real authentication before this is used for anything but the capstone demo.
The id is never taken from a request body, so a model cannot choose whose data it acts on.

Confirmation: the submit / cancel / modify endpoints take `confirmed` in the body. The app sets it
to true only after the employee clicks Confirm; without it they answer 409 `confirmation_required`.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/hrms", tags=["hrms"])

# How a service error code maps to an HTTP status. Anything not listed is a plain 400.
STATUS = {
    "unknown_actor": 401, "not_allowed": 403, "not_found": 404, "not_entitled": 404,
    "invalid_request": 422, "document_required": 422, "bad_decision": 422, "note_required": 422,
    "modify_rule": 422, "invalid_state": 409, "confirmation_required": 409, "no_change": 409,
    "too_late": 409,
}


def actor(x_employee_id: Optional[str] = Header(default=None)) -> str:
    if not x_employee_id:
        raise HTTPException(401, "Send the logged-in employee's id in the X-Employee-Id header (demo login).")
    return x_employee_id


def reply(result: dict, success: int = 200) -> JSONResponse:
    return JSONResponse(result, status_code=success if result["ok"] else STATUS.get(result["error"], 400))


class LeaveDates(BaseModel):
    leave_code: str
    start_date: str
    end_date: str
    half_day: bool = False


class NewDraft(LeaveDates):
    reason: Optional[str] = None


class Submit(BaseModel):
    confirmed: bool = False
    doc_attached: bool = False


class Cancel(BaseModel):
    confirmed: bool = False


class Modify(BaseModel):
    start_date: str
    end_date: str
    half_day: Optional[bool] = None
    confirmed: bool = False


class Decision(BaseModel):
    decision: str  # APPROVE, REJECT or SEND_BACK
    note: Optional[str] = None


# --- read ----------------------------------------------------------------
@router.get("/me")
def me(who: str = Depends(actor)):
    return reply(service.get_employee(who))


@router.get("/employees/{emp_id}")
def employee(emp_id: str, who: str = Depends(actor)):
    return reply(service.get_employee(who, emp_id))


@router.get("/balance")
def balance(leave_code: Optional[str] = None, emp_id: Optional[str] = None, who: str = Depends(actor)):
    return reply(service.get_leave_balance(who, emp_id, leave_code))


@router.get("/holidays")
def holidays(type: Optional[str] = None, who: str = Depends(actor)):
    return reply(service.get_holidays(who, type))


@router.get("/manager")
def manager(emp_id: Optional[str] = None, who: str = Depends(actor)):
    return reply(service.get_manager(who, emp_id))


@router.get("/requests")
def requests_list(emp_id: Optional[str] = None, status: Optional[str] = None, who: str = Depends(actor)):
    return reply(service.list_requests(who, emp_id, status))


# --- apply for leave -----------------------------------------------------
@router.post("/leave/check")
def check(body: LeaveDates, who: str = Depends(actor)):
    return reply(service.check_leave_request(who, body.leave_code, body.start_date, body.end_date, body.half_day))


@router.post("/requests")
def create(body: NewDraft, who: str = Depends(actor)):
    return reply(service.create_leave_draft(
        who, body.leave_code, body.start_date, body.end_date, body.half_day, body.reason), success=201)


@router.post("/requests/{request_id}/submit")
def submit(request_id: int, body: Submit, who: str = Depends(actor)):
    return reply(service.submit_request(who, request_id, body.confirmed, body.doc_attached))


@router.post("/requests/{request_id}/cancel")
def cancel(request_id: int, body: Cancel, who: str = Depends(actor)):
    return reply(service.cancel_request(who, request_id, body.confirmed))


@router.post("/requests/{request_id}/modify")
def modify(request_id: int, body: Modify, who: str = Depends(actor)):
    return reply(service.modify_request(
        who, request_id, body.start_date, body.end_date, body.half_day, body.confirmed))


# --- manager -------------------------------------------------------------
@router.get("/approvals")
def approvals(who: str = Depends(actor)):
    return reply(service.list_pending_approvals(who))


@router.post("/requests/{request_id}/decision")
def decide(request_id: int, body: Decision, who: str = Depends(actor)):
    return reply(service.decide_request(who, request_id, body.decision, body.note))

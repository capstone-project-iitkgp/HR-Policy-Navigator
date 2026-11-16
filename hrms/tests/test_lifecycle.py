"""Draft, submit, cancel and modify: state changes, balances, confirmation and access rules."""

from contextlib import closing

from hrms import db, service


def balance(actor, code):
    return service.get_leave_balance(actor, leave_code=code)["balances"][0]


def request(actor, request_id):
    return next(r for r in service.list_requests(actor)["requests"] if r["id"] == request_id)


def find(actor, status, code=None):
    return next(r for r in service.list_requests(actor, status=status)["requests"]
                if code is None or r["leave_code"] == code)


def draft(actor="D010", code="CL", start="2026-12-02", end="2026-12-02", **kw):
    result = service.create_leave_draft(actor, code, start, end, **kw)
    assert result["ok"], result
    return result["request_id"]


def pending_matches_requests(actor, code):
    """The balance's `pending` equals the days of that employee's PENDING requests."""
    held = sum(r["days"] for r in service.list_requests(actor, status="PENDING")["requests"]
               if r["leave_code"] == code)
    return balance(actor, code)["pending"] == held


# --- drafts --------------------------------------------------------------
def test_draft_is_saved_without_touching_the_balance():
    before = balance("D010", "CL")
    rid = draft(reason="Family visit")
    saved = request("D010", rid)
    assert saved["status"] == "DRAFT" and saved["days"] == 1 and saved["reason"] == "Family visit"
    assert saved["applied_on"] is None and saved["created_on"] == "2026-11-16"
    assert balance("D010", "CL") == before


def test_invalid_draft_is_refused_and_nothing_is_saved():
    before = len(service.list_requests("D020")["requests"])
    r = service.create_leave_draft("D020", "CL", "2026-12-01", "2026-12-01")  # zero balance
    assert not r["ok"] and r["error"] == "invalid_request"
    assert r["errors"][0]["code"] == "insufficient_balance"
    assert len(service.list_requests("D020")["requests"]) == before


def test_a_second_draft_for_the_same_dates_is_refused():
    draft()
    r = service.create_leave_draft("D010", "CL", "2026-12-02", "2026-12-02")
    assert not r["ok"] and r["errors"][0]["code"] == "overlap"


def test_preview_rechecks_a_saved_request_without_clashing_with_itself():
    rid = draft()
    preview = service.preview_request("D010", rid)
    assert preview["ok"] and preview["valid"] and preview["status"] == "DRAFT" and preview["days"] == 1
    assert service.preview_request("D006", rid)["error"] == "not_found"
    assert request("D010", rid)["status"] == "DRAFT"


# --- submit --------------------------------------------------------------
def test_submit_needs_the_employees_confirmation():
    rid = draft()
    r = service.submit_request("D010", rid)  # what an agent tool would do
    assert not r["ok"] and r["error"] == "confirmation_required" and r["preview"]["valid"]
    assert request("D010", rid)["status"] == "DRAFT"


def test_confirmed_submit_files_the_request_and_holds_the_days():
    before = balance("D010", "CL")
    rid = draft(start="2026-12-02", end="2026-12-03")
    r = service.submit_request("D010", rid, confirmed=True)
    assert r["ok"] and r["status"] == "PENDING" and r["approver"]["emp_id"] == "D003"
    saved = request("D010", rid)
    assert saved["status"] == "PENDING" and saved["applied_on"] == "2026-11-16"
    assert balance("D010", "CL")["pending"] == before["pending"] + 2
    assert balance("D010", "CL")["available"] == before["available"] - 2
    assert pending_matches_requests("D010", "CL")


def test_submit_blocks_until_a_required_document_is_attached():
    rid = draft("D020", "SL", "2026-11-13", "2026-11-13")  # D020 already used 2 sick days
    r = service.submit_request("D020", rid, confirmed=True)
    assert not r["ok"] and r["error"] == "document_required"
    assert request("D020", rid)["status"] == "DRAFT"
    ok = service.submit_request("D020", rid, confirmed=True, doc_attached=True)
    assert ok["ok"] and request("D020", rid)["doc_attached"] == 1


def test_submit_checks_the_rules_again():
    rid = draft("D009", "EL", "2026-12-07", "2026-12-07")
    with closing(db.get_conn()) as conn:  # someone else's approved leave appears in the meantime
        conn.execute("UPDATE leave_requests SET status = 'DRAFT' WHERE id = ?", (rid,))
        conn.execute("INSERT INTO leave_requests (emp_id, leave_code, start_date, end_date, days, status,"
                     " created_on) VALUES ('D009', 'EL', '2026-12-07', '2026-12-07', 1, 'APPROVED', '2026-11-16')")
        conn.commit()
    r = service.submit_request("D009", rid, confirmed=True)
    assert not r["ok"] and r["errors"][0]["code"] == "overlap"


def test_only_drafts_and_sent_back_requests_can_be_submitted():
    rid = find("D024", "PENDING")["id"]
    assert service.submit_request("D024", rid, confirmed=True)["error"] == "invalid_state"


def test_a_request_sent_back_can_be_resubmitted():
    rid = find("B021", "SENT_BACK")["id"]
    assert service.submit_request("B021", rid, confirmed=True)["status"] == "PENDING"
    assert request("B021", rid)["decision_note"] is None
    assert pending_matches_requests("B021", "CL")


# --- cancel --------------------------------------------------------------
def test_a_draft_cancels_without_confirmation():
    rid = draft()
    assert service.cancel_request("D010", rid)["status"] == "CANCELLED"
    assert request("D010", rid)["decision_note"] == "Cancelled by employee"


def test_cancelling_a_pending_request_releases_the_days():
    before = balance("D010", "CL")
    rid = draft()
    service.submit_request("D010", rid, confirmed=True)
    assert service.cancel_request("D010", rid)["error"] == "confirmation_required"
    assert request("D010", rid)["status"] == "PENDING"
    assert service.cancel_request("D010", rid, confirmed=True)["status"] == "CANCELLED"
    assert balance("D010", "CL") == before


def test_cancelling_approved_leave_gives_the_days_back():
    before = balance("D013", "EL")  # D013 has approved EL on 14-16 Dec (3 days)
    rid = find("D013", "APPROVED")["id"]
    assert service.cancel_request("D013", rid)["error"] == "confirmation_required"
    assert service.cancel_request("D013", rid, confirmed=True)["was"] == "APPROVED"
    after = balance("D013", "EL")
    assert after["used"] == before["used"] - 3 and after["available"] == before["available"] + 3


def test_approved_leave_cannot_be_cancelled_after_its_end_date():
    rid = find("D016", "APPROVED")["id"]  # sick leave 2-3 Nov, already over
    r = service.cancel_request("D016", rid, confirmed=True)
    assert not r["ok"] and r["error"] == "too_late"


def test_rejected_and_cancelled_requests_stay_as_they_are():
    for actor, status in (("D019", "REJECTED"), ("D018", "CANCELLED")):
        rid = find(actor, status)["id"]
        assert service.cancel_request(actor, rid, confirmed=True)["error"] == "invalid_state"
        assert service.modify_request(actor, rid, "2026-12-01", "2026-12-01")["error"] == "invalid_state"


# --- modify --------------------------------------------------------------
def test_draft_dates_can_be_changed():
    rid = draft()
    r = service.modify_request("D010", rid, "2026-12-02", "2026-12-03")
    assert r["ok"] and r["status"] == "DRAFT" and request("D010", rid)["days"] == 2


def test_changing_a_pending_request_updates_the_held_days():
    before = balance("D010", "CL")
    rid = draft()
    service.submit_request("D010", rid, confirmed=True)
    assert service.modify_request("D010", rid, "2026-12-02", "2026-12-04")["status"] == "PENDING"  # 3 days
    assert balance("D010", "CL")["pending"] == before["pending"] + 3
    assert pending_matches_requests("D010", "CL")


def test_changing_approved_leave_needs_confirmation_and_a_new_approval():
    rid = find("D013", "APPROVED")["id"]  # EL 14-16 Dec
    before = balance("D013", "EL")
    r = service.modify_request("D013", rid, "2026-12-15", "2026-12-17")
    assert r["error"] == "confirmation_required"
    assert request("D013", rid)["status"] == "APPROVED"
    r = service.modify_request("D013", rid, "2026-12-15", "2026-12-17", confirmed=True)
    assert r["ok"] and r["status"] == "PENDING"
    after = balance("D013", "EL")
    assert after["used"] == before["used"] - 3 and after["pending"] == before["pending"] + 3
    assert after["available"] == before["available"]  # same number of days, only moved
    saved = request("D013", rid)
    assert saved["decided_by"] is None and saved["start_date"] == "2026-12-15"


def test_def_new_start_date_must_lie_within_the_old_dates():
    rid = find("D013", "APPROVED")["id"]
    r = service.modify_request("D013", rid, "2026-12-17", "2026-12-18", confirmed=True)
    assert not r["ok"] and r["error"] == "modify_rule"


def test_modify_is_checked_against_the_rules_and_leaves_the_request_unchanged():
    rid = draft()
    r = service.modify_request("D010", rid, "2026-12-02", "2026-12-30")  # far too long
    assert not r["ok"] and r["error"] == "invalid_request"
    assert request("D010", rid)["end_date"] == "2026-12-02"


def test_modify_with_the_same_dates_is_reported():
    rid = draft()
    assert service.modify_request("D010", rid, "2026-12-02", "2026-12-02")["error"] == "no_change"


# --- access and audit ----------------------------------------------------
def test_nobody_else_can_touch_a_request():
    rid = draft()
    for call in (lambda a: service.submit_request(a, rid, confirmed=True),
                 lambda a: service.cancel_request(a, rid, confirmed=True),
                 lambda a: service.modify_request(a, rid, "2026-12-03", "2026-12-03")):
        for outsider in ("D006", "D003", "D001"):  # a colleague, the manager and HR
            r = call(outsider)
            assert not r["ok"] and r["error"] == "not_found"
        assert call("D010").get("error") != "not_found"
    assert request("D010", rid)["status"] in ("DRAFT", "PENDING", "CANCELLED")


def test_a_missing_request_looks_the_same_as_someone_elses():
    rid = draft()
    assert service.cancel_request("D006", rid)["message"].replace(str(rid), "N") == \
        service.cancel_request("D006", 99999)["message"].replace("99999", "N")


def test_every_step_is_audited_with_the_request_id():
    rid = draft()
    service.submit_request("D010", rid)                       # refused: no confirmation
    service.submit_request("D010", rid, confirmed=True)
    service.cancel_request("D006", rid, confirmed=True)       # refused: not theirs
    with closing(db.get_conn()) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT actor, action, request_id, ok FROM audit_log WHERE request_id = ? ORDER BY id", (rid,))]
    assert [(r["actor"], r["action"], r["ok"]) for r in rows] == [
        ("D010", "create_leave_draft", 1), ("D010", "submit_request", 0),
        ("D010", "submit_request", 1), ("D006", "cancel_request", 0)]


def test_balances_stay_consistent_after_a_mix_of_actions():
    a, b = draft(), draft(start="2026-12-09", end="2026-12-10")
    service.submit_request("D010", a, confirmed=True)
    service.submit_request("D010", b, confirmed=True)
    service.modify_request("D010", b, "2026-12-09", "2026-12-09")
    service.cancel_request("D010", a, confirmed=True)
    assert pending_matches_requests("D010", "CL")
    with closing(db.get_conn()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM leave_balances WHERE entitled + carried_forward - used - pending < 0").fetchone()[0] == 0

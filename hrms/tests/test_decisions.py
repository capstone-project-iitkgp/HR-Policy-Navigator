"""Manager decisions: who may decide, what each decision does to the request and the balance."""

from contextlib import closing

from hrms import db, service


def balance(actor, code):
    return service.get_leave_balance(actor, leave_code=code)["balances"][0]


def inbox(actor):
    return service.list_pending_approvals(actor)["approvals"]


def request(actor, request_id):
    return next(r for r in service.list_requests(actor)["requests"] if r["id"] == request_id)


def file_request(actor, code="CL", start="2026-12-02", end="2026-12-02"):
    rid = service.create_leave_draft(actor, code, start, end)["request_id"]
    assert service.submit_request(actor, rid, confirmed=True)["ok"]
    return rid


def seeded_pending(actor):
    return inbox(actor)[0]["id"]  # D003 has D024's CL request; B005 has B024's


def test_inbox_shows_only_requests_the_actor_decides():
    mine = inbox("D003")
    assert [(r["emp_id"], r["leave_code"], r["days"]) for r in mine] == [("D024", "CL", 3)]
    assert mine[0]["employee_name"] == "Prerna Bose" and mine[0]["balance"]["pending"] == 3
    assert inbox("D005") == [] and inbox("D001") == [] and inbox("D006") == []
    assert [r["emp_id"] for r in inbox("B005")] == ["B024"]


def test_approve_moves_the_days_from_pending_to_used():
    rid, before = seeded_pending("D003"), balance("D024", "CL")
    r = service.decide_request("D003", rid, "approve")
    assert r["ok"] and r["status"] == "APPROVED" and r["employee_id"] == "D024"
    after = balance("D024", "CL")
    assert after["pending"] == before["pending"] - 3 and after["used"] == before["used"] + 3
    assert after["available"] == before["available"]
    saved = request("D024", rid)
    assert saved["decided_by"] == "D003" and saved["decided_on"] == "2026-11-16" and saved["decision_note"] is None
    assert inbox("D003") == []


def test_reject_needs_a_note_and_releases_the_days():
    rid, before = seeded_pending("D003"), balance("D024", "CL")
    assert service.decide_request("D003", rid, "REJECT")["error"] == "note_required"
    assert service.decide_request("D003", rid, "REJECT", note="   ")["error"] == "note_required"
    assert request("D024", rid)["status"] == "PENDING"
    r = service.decide_request("D003", rid, "REJECT", note="Release week, please move it")
    assert r["status"] == "REJECTED"
    after = balance("D024", "CL")
    assert after["pending"] == 0 and after["available"] == before["available"] + 3
    assert request("D024", rid)["decision_note"] == "Release week, please move it"


def test_send_back_lets_the_employee_fix_and_resubmit():
    rid = seeded_pending("B005")
    assert service.decide_request("B005", rid, "SEND_BACK", note="Please add a reason")["status"] == "SENT_BACK"
    assert service.submit_request("B024", rid, confirmed=True)["status"] == "PENDING"
    assert [r["id"] for r in inbox("B005")] == [rid]
    assert request("B024", rid)["decision_note"] is None


def test_only_the_approver_can_decide():
    rid = seeded_pending("D003")
    for outsider in ("D005", "D001", "D002", "D024"):  # other manager, HR, skip-level manager, the employee
        r = service.decide_request(outsider, rid, "APPROVE")
        assert not r["ok"] and r["error"] == "not_found", outsider
    assert request("D024", rid)["status"] == "PENDING"


def test_a_missing_request_looks_like_someone_elses():
    rid = seeded_pending("D003")
    mine = service.decide_request("D005", rid, "APPROVE")["message"]
    missing = service.decide_request("D005", 99999, "APPROVE")["message"]
    assert mine.replace(str(rid), "N") == missing.replace("99999", "N")


def test_hr_decides_for_people_without_a_manager():
    rid = file_request("D002")  # the director has no manager
    assert [r["id"] for r in inbox("D001")] == [rid]
    assert rid not in [r["id"] for r in inbox("D003")]
    assert service.decide_request("D001", rid, "APPROVE")["status"] == "APPROVED"


def test_hr_own_leave_goes_to_their_manager_not_themselves():
    rid = file_request("D001")
    assert [r["id"] for r in inbox("D002")] == [rid] and inbox("D001") == []
    assert service.decide_request("D001", rid, "APPROVE")["error"] == "not_found"


def test_only_pending_requests_can_be_decided_and_decisions_are_checked():
    rid = seeded_pending("D003")
    assert service.decide_request("D003", rid, "MAYBE")["error"] == "bad_decision"
    service.decide_request("D003", rid, "APPROVE")
    assert service.decide_request("D003", rid, "REJECT", note="x")["error"] == "invalid_state"
    approved = next(r for r in service.list_requests("D013", status="APPROVED")["requests"])
    assert service.decide_request("D004", approved["id"], "APPROVE")["error"] == "invalid_state"


def test_employee_can_cancel_after_approval_and_the_days_return():
    rid, start = seeded_pending("D003"), balance("D024", "CL")
    service.decide_request("D003", rid, "APPROVE")
    assert service.cancel_request("D024", rid, confirmed=True)["was"] == "APPROVED"
    assert balance("D024", "CL") == dict(start, pending=0, available=start["available"] + 3)


def test_changed_approved_leave_goes_back_to_the_manager():
    rid = service.list_requests("D013", status="APPROVED")["requests"][0]["id"]  # EL 14-16 Dec, approver D004
    before = balance("D013", "EL")
    assert service.modify_request("D013", rid, "2026-12-15", "2026-12-17", confirmed=True)["status"] == "PENDING"
    assert [r["id"] for r in inbox("D004")] == [rid]
    service.decide_request("D004", rid, "APPROVE")
    assert balance("D013", "EL") == before and request("D013", rid)["start_date"] == "2026-12-15"


def test_decisions_are_audited_including_refusals():
    rid = seeded_pending("D003")
    service.decide_request("D005", rid, "APPROVE")
    service.decide_request("D003", rid, "APPROVE")
    with closing(db.get_conn()) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT actor, action, ok FROM audit_log WHERE request_id = ? AND action = 'decide_request' ORDER BY id", (rid,))]
    assert [(r["actor"], r["ok"]) for r in rows] == [("D005", 0), ("D003", 1)]

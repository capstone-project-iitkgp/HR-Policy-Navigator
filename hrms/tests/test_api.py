"""HTTP layer: status codes, the demo login header and an end-to-end leave flow."""

from fastapi.testclient import TestClient

from hr_navigator.main import app

client = TestClient(app)


def call(method, path, who=None, **kwargs):
    headers = {"X-Employee-Id": who} if who else {}
    return client.request(method, "/hrms" + path, headers=headers, **kwargs)


def draft(who="D010", start="2026-12-02", end="2026-12-02"):
    r = call("POST", "/requests", who, json={"leave_code": "CL", "start_date": start, "end_date": end})
    assert r.status_code == 201, r.text
    return r.json()["request_id"]


def test_existing_health_check_still_works():
    assert client.get("/health").json() == {"status": "ok"}


def test_hrms_routes_are_part_of_the_app():
    paths = client.get("/openapi.json").json()["paths"]
    assert "/hrms/requests/{request_id}/submit" in paths and "/hrms/approvals" in paths


def test_login_header_is_required_and_must_name_a_real_employee():
    assert call("GET", "/me").status_code == 401
    r = call("GET", "/me", "X999")
    assert r.status_code == 401 and r.json()["error"] == "unknown_actor"


def test_reading_own_data():
    assert call("GET", "/me", "D010").json()["employee"]["emp_id"] == "D010"
    r = call("GET", "/balance", "D010", params={"leave_code": "EL"})
    assert r.status_code == 200 and r.json()["balances"][0]["available"] == 6.0
    assert len(call("GET", "/holidays", "D010").json()["holidays"]) == 15
    assert len(call("GET", "/holidays", "D010", params={"type": "restricted"}).json()["holidays"]) == 5
    assert call("GET", "/manager", "D010").json()["manager"]["emp_id"] == "D003"


def test_other_peoples_data_is_forbidden_and_missing_entitlement_is_404():
    assert call("GET", "/employees/D006", "D010").status_code == 403
    assert call("GET", "/balance", "D010", params={"emp_id": "D006"}).status_code == 403
    assert call("GET", "/balance", "D014", params={"leave_code": "EL"}).status_code == 404


def test_check_endpoint_reports_the_verdict_without_saving():
    r = call("POST", "/leave/check", "D006",
             json={"leave_code": "CL", "start_date": "2026-12-04", "end_date": "2026-12-07"})
    assert r.status_code == 200 and r.json()["valid"] and r.json()["days"] == 4
    assert call("GET", "/requests", "D006").json()["requests"] == []


def test_invalid_draft_is_422_with_the_reasons():
    r = call("POST", "/requests", "D020", json={"leave_code": "CL", "start_date": "2026-12-01", "end_date": "2026-12-01"})
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "insufficient_balance"


def test_malformed_input_is_rejected_before_it_reaches_the_service():
    assert call("POST", "/requests", "D010", json={"leave_code": "CL"}).status_code == 422
    assert call("POST", "/requests/abc/submit", "D010", json={}).status_code == 422


def test_the_acting_employee_cannot_be_chosen_in_the_body():
    r = call("POST", "/requests", "D010", json={
        "leave_code": "CL", "start_date": "2026-12-02", "end_date": "2026-12-02", "emp_id": "D006"})
    rid = r.json()["request_id"]
    assert [q["id"] for q in call("GET", "/requests", "D010").json()["requests"] if q["id"] == rid] == [rid]
    assert call("GET", "/requests", "D006").json()["requests"] == []


def test_submit_needs_confirmation_over_http():
    rid = draft()
    r = call("POST", f"/requests/{rid}/submit", "D010", json={})
    assert r.status_code == 409 and r.json()["error"] == "confirmation_required"
    assert call("POST", f"/requests/{rid}/submit", "D010", json={"confirmed": True}).json()["status"] == "PENDING"
    assert call("POST", f"/requests/{rid}/submit", "D010", json={"confirmed": True}).status_code == 409  # already filed
    assert call("POST", f"/requests/{rid}/submit", "D006", json={"confirmed": True}).status_code == 404


def test_full_flow_employee_to_manager_and_back():
    rid = draft()
    call("POST", f"/requests/{rid}/submit", "D010", json={"confirmed": True})
    assert [a["id"] for a in call("GET", "/approvals", "D003").json()["approvals"] if a["emp_id"] == "D010"] == [rid]
    assert call("GET", "/approvals", "D006").json()["approvals"] == []
    assert call("POST", f"/requests/{rid}/decision", "D005", json={"decision": "APPROVE"}).status_code == 404
    assert call("POST", f"/requests/{rid}/decision", "D003", json={"decision": "REJECT"}).status_code == 422
    done = call("POST", f"/requests/{rid}/decision", "D003", json={"decision": "APPROVE"})
    assert done.status_code == 200 and done.json()["status"] == "APPROVED"
    assert [q["id"] for q in call("GET", "/requests", "D010", params={"status": "approved"}).json()["requests"]] == [rid]
    cancel = call("POST", f"/requests/{rid}/cancel", "D010", json={})
    assert cancel.status_code == 409
    assert call("POST", f"/requests/{rid}/cancel", "D010", json={"confirmed": True}).json()["status"] == "CANCELLED"


def test_modify_over_http():
    rid = draft()
    r = call("POST", f"/requests/{rid}/modify", "D010", json={"start_date": "2026-12-02", "end_date": "2026-12-03"})
    assert r.status_code == 200 and r.json()["days"] == 2
    same = call("POST", f"/requests/{rid}/modify", "D010", json={"start_date": "2026-12-02", "end_date": "2026-12-03"})
    assert same.status_code == 409 and same.json()["error"] == "no_change"

"""Read-side service: correct answers, access rules and the audit trail."""

from contextlib import closing

from hrms import db, service


def audit_rows():
    with closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id")]


def balance(result, code):
    return next(b for b in result["balances"] if b["leave_code"] == code)


def test_employee_sees_own_balance_with_available_days():
    r = service.get_leave_balance("D010", leave_code="EL")
    assert r["ok"] and r["as_of"] == "2026-11-16"
    b = balance(r, "EL")
    assert b["entitled"] == 10.0 and b["used"] == 4.0 and b["available"] == 6.0  # Q001 joiner


def test_zero_balance_is_reported_as_zero():
    assert balance(service.get_leave_balance("D020", leave_code="CL"), "CL")["available"] == 0


def test_pending_days_are_held_back_from_available():
    b = balance(service.get_leave_balance("D024", leave_code="CL"), "CL")
    assert b["pending"] == 3.0 and b["available"] == b["entitled"] - b["used"] - 3.0


def test_leave_type_the_employee_is_not_entitled_to():
    r = service.get_leave_balance("D014", leave_code="EL")  # on probation
    assert not r["ok"] and r["error"] == "not_entitled" and "FT_PROBATION" in r["message"]


def test_employee_cannot_read_a_colleague():
    r = service.get_leave_balance("D006", "D010")
    assert not r["ok"] and r["error"] == "not_allowed"


def test_missing_and_forbidden_targets_look_the_same():
    assert service.get_employee("D006", "NOPE") == service.get_employee("D006", "D010")


def test_manager_sees_reports_but_not_other_teams():
    assert service.get_leave_balance("D003", "D010")["ok"]   # D010 reports to D003
    assert not service.get_leave_balance("D003", "D019")["ok"]  # D019 reports to D005


def test_manager_reaches_indirect_reports():
    assert service.get_leave_balance("D002", "D010")["ok"]  # D002 -> D003 -> D010


def test_manager_gets_limited_profile_fields():
    fields = service.get_employee("D003", "D010")["employee"].keys()
    assert "gender" not in fields and "num_children" not in fields
    assert "gender" in service.get_employee("D010")["employee"]


def test_hr_sees_everyone_including_the_other_org():
    assert service.get_leave_balance("D001", "B010")["ok"]


def test_manager_lookup():
    assert service.get_manager("D010")["manager"]["emp_id"] == "D003"
    top = service.get_manager("D002")
    assert top["ok"] and top["manager"] is None


def test_holidays_are_per_organisation():
    def_list = service.get_holidays("D010")
    bld_list = service.get_holidays("B010")
    assert def_list["policy_org"] == "DEF" and "restricted" in def_list["note"]
    assert {h["type"] for h in bld_list["holidays"]} == {"compulsory", "public"}
    assert all(h["date"].startswith("2026-") for h in def_list["holidays"])
    restricted = service.get_holidays("D010", holiday_type="restricted")["holidays"]
    assert len(restricted) == 5


def test_list_requests_filters_by_status():
    r = service.list_requests("D024", status="pending")
    assert [q["status"] for q in r["requests"]] == ["PENDING"]


def test_unknown_actor_is_rejected_and_audited():
    r = service.get_manager("X999")
    assert not r["ok"] and r["error"] == "unknown_actor"
    assert audit_rows()[-1]["actor"] == "unknown" and audit_rows()[-1]["ok"] == 0


def test_every_call_is_audited_including_denied_ones():
    service.get_leave_balance("D010")
    service.get_leave_balance("D006", "D010")  # denied
    rows = audit_rows()
    assert [(r["actor"], r["action"], r["ok"]) for r in rows] == [
        ("D010", "get_leave_balance", 1), ("D006", "get_leave_balance", 0)]
    assert "not_allowed" in rows[1]["details"]

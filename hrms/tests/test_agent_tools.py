"""The agent's HRMS tools: what is exposed, input validation, trimmed output and the audit channel."""

import json
from contextlib import closing

import pytest

from hrms import agent_tools, db, service
from hrms.agent_tools import run_tool, tool_schemas

EXPECTED_TOOLS = {"get_my_profile", "get_leave_balance", "get_holidays", "get_my_manager", "list_my_requests",
                  "check_leave_request", "create_leave_draft", "cancel_leave_request"}


def audit(where="1=1"):
    with closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT actor, action, channel, ok FROM audit_log WHERE %s ORDER BY id" % where)]


def draft(who="D010", start="2026-12-02", end="2026-12-02", **extra):
    result = run_tool(who, "create_leave_draft", dict(leave_code="CL", start_date=start, end_date=end, **extra))
    assert result["ok"], result
    return result["request_id"]


# --- what is exposed -----------------------------------------------------
def test_exactly_the_planned_tools_are_exposed():
    assert set(agent_tools.TOOLS) == EXPECTED_TOOLS
    assert {s["function"]["name"] for s in tool_schemas()} == EXPECTED_TOOLS


def test_nothing_that_files_changes_or_approves_leave_is_available():
    for forbidden in ("submit_request", "modify_request", "decide_request", "list_pending_approvals", "preview_request"):
        assert forbidden not in agent_tools.TOOLS
        assert run_tool("D010", forbidden)["error"] == "unknown_tool"


def test_no_tool_takes_an_employee_id_or_a_confirmation():
    for schema in tool_schemas():
        params = schema["function"]["parameters"]
        assert params.get("additionalProperties") is False, schema["function"]["name"]
        assert not {"emp_id", "employee_id", "actor_id", "actor", "confirmed", "doc_attached"} & set(params.get("properties", {}))


def test_schemas_are_json_and_small_enough_for_an_8k_window():
    text = json.dumps(tool_schemas())
    assert json.loads(text) and "title" not in text
    assert len(text) < 5000  # roughly 1,250 tokens of an 8,192-token window


def test_schema_follows_the_function_calling_format():
    s = next(s for s in tool_schemas() if s["function"]["name"] == "check_leave_request")
    assert s["type"] == "function" and s["function"]["description"]
    assert set(s["function"]["parameters"]["required"]) == {"leave_code", "start_date", "end_date"}


# --- argument checking ---------------------------------------------------
def test_arguments_may_be_a_dict_or_the_json_string_a_model_produces():
    assert run_tool("D010", "get_leave_balance", {"leave_code": "EL"})["ok"]
    assert run_tool("D010", "get_leave_balance", '{"leave_code": "EL"}')["ok"]
    assert run_tool("D010", "get_my_manager", None)["ok"] and run_tool("D010", "get_my_manager", "")["ok"]


def test_leave_codes_are_case_insensitive():
    r = run_tool("D010", "get_leave_balance", {"leave_code": "el"})
    assert [b["leave_code"] for b in r["balances"]] == ["EL"]


def test_malformed_arguments_get_a_helpful_error_not_an_exception():
    for bad in ("{leave_code: EL", "[1, 2]", "42"):
        r = run_tool("D010", "get_leave_balance", bad)
        assert not r["ok"] and r["error"] == "bad_arguments" and "JSON object" in r["message"]


def test_unknown_tool_lists_the_real_ones():
    r = run_tool("D010", "get_everyones_salary", {})
    assert r["error"] == "unknown_tool" and "get_leave_balance" in r["message"]


@pytest.mark.parametrize("extra", [{"emp_id": "D006"}, {"actor_id": "D001"}, {"confirmed": True}])
def test_a_model_cannot_pass_an_identity_or_a_confirmation(extra):
    r = run_tool("D010", "check_leave_request", dict(leave_code="CL", start_date="2026-12-02", end_date="2026-12-02", **extra))
    assert not r["ok"] and r["error"] == "bad_arguments"
    assert next(iter(extra)) in json.dumps(r["problems"])
    assert audit("action = 'check_leave_request'") == []  # it never reached the HR system


def test_missing_and_wrong_arguments_are_listed_by_name():
    r = run_tool("D010", "create_leave_draft", {"leave_code": "XYZ", "start_date": "tomorrow"})
    fields = {p["argument"] for p in r["problems"]}
    assert r["error"] == "bad_arguments" and fields == {"leave_code", "start_date", "end_date"}


def test_dates_must_be_real_and_are_normalised():
    assert run_tool("D010", "check_leave_request", dict(leave_code="CL", start_date="2026-02-30", end_date="2026-03-01"))["error"] == "bad_arguments"
    rid = run_tool("D010", "create_leave_draft", dict(leave_code="CL", start_date="20261202", end_date="2026-12-02"))["request_id"]
    saved = next(r for r in service.list_requests("D010")["requests"] if r["id"] == rid)
    assert saved["start_date"] == "2026-12-02"


# --- results -------------------------------------------------------------
def test_balance_result_is_compact_and_correct():
    r = run_tool("D010", "get_leave_balance", {"leave_code": "EL"})
    assert r["balances"] == [{"leave_code": "EL", "leave_name": "Earned Leave", "entitled": 10.0,
                              "carried_forward": 0.0, "used": 4.0, "pending": 0.0, "available": 6.0}]


def test_the_tools_always_act_as_the_logged_in_employee():
    mine = run_tool("D010", "get_leave_balance", {"leave_code": "EL"})["balances"][0]["available"]
    theirs = run_tool("D020", "get_leave_balance", {"leave_code": "EL"})["balances"][0]["available"]
    assert mine != theirs
    assert run_tool("X999", "get_my_profile")["error"] == "unknown_actor"


def test_profile_includes_todays_date_for_the_agents_date_maths():
    r = run_tool("D010", "get_my_profile")
    assert r["today"] == "2026-11-16" and r["policy_org"] == "DEF" and "email" not in r


def test_not_entitled_comes_back_as_an_error_the_agent_can_explain():
    r = run_tool("D014", "get_leave_balance", {"leave_code": "EL"})
    assert not r["ok"] and r["error"] == "not_entitled" and "FT_PROBATION" in r["message"]


def test_holidays_and_manager():
    h = run_tool("D010", "get_holidays", {"holiday_type": "restricted"})
    assert len(h["holidays"]) == 5 and "restricted" in h["note"]
    assert run_tool("D010", "get_my_manager")["manager"] == {"name": "Anita Rao", "designation": "Programme Lead"}
    assert run_tool("D002", "get_my_manager")["manager"] is None


def test_check_result_is_compact_and_readable():
    r = run_tool("D006", "check_leave_request", dict(leave_code="CL", start_date="2026-12-04", end_date="2026-12-07"))
    assert r["ok"] and r["valid"] and r["days"] == 4 and r["approver"] == "Anita Rao (Programme Lead)"
    assert "Saturday and Sunday also count" in r["warnings"][0]
    assert "skipped" not in r and "approver_id" not in r


def test_a_failed_check_explains_why():
    r = run_tool("D020", "check_leave_request", dict(leave_code="CL", start_date="2026-12-01", end_date="2026-12-01"))
    assert r["ok"] and not r["valid"] and r["errors"][0]["code"] == "insufficient_balance"


def test_requests_list_is_capped():
    with closing(db.get_conn()) as conn:
        for day in range(1, 13):
            conn.execute("INSERT INTO leave_requests (emp_id, leave_code, start_date, end_date, days, status, created_on)"
                         " VALUES ('D010', 'CL', '2026-01-%02d', '2026-01-%02d', 1, 'CANCELLED', '2026-01-01')" % (day, day))
        conn.commit()
    r = run_tool("D010", "list_my_requests", {"status": "cancelled"})
    assert len(r["requests"]) == agent_tools.MAX_REQUESTS_SHOWN and "Showing 10 of 12" in r["note"]
    assert set(r["requests"][0]) == {"id", "leave_code", "start_date", "end_date", "days", "status", "decision_note"}


# --- drafts and the confirmation boundary --------------------------------
def test_draft_is_saved_but_not_filed_and_the_agent_is_told_so():
    before = service.get_leave_balance("D010", leave_code="CL")["balances"][0]
    r = run_tool("D010", "create_leave_draft", dict(leave_code="CL", start_date="2026-12-02", end_date="2026-12-03", reason="Trip"))
    assert r["ok"] and r["status"] == "DRAFT" and r["check"]["days"] == 2
    assert "cannot submit" in r["next_step"]
    assert service.get_leave_balance("D010", leave_code="CL")["balances"][0] == before  # nothing held yet


def test_an_invalid_draft_returns_the_rule_errors_and_saves_nothing():
    n = len(service.list_requests("D020")["requests"])
    r = run_tool("D020", "create_leave_draft", dict(leave_code="CL", start_date="2026-12-01", end_date="2026-12-01"))
    assert not r["ok"] and r["error"] == "invalid_request" and r["errors"][0]["code"] == "insufficient_balance"
    assert len(service.list_requests("D020")["requests"]) == n


def test_the_agent_can_discard_a_draft_but_not_cancel_filed_leave():
    rid = draft()
    assert run_tool("D010", "cancel_leave_request", {"request_id": rid})["status"] == "CANCELLED"
    pending = service.list_requests("D024", status="PENDING")["requests"][0]
    r = run_tool("D024", "cancel_leave_request", {"request_id": pending["id"]})
    assert not r["ok"] and r["error"] == "confirmation_required" and "app" in r["next_step"] and "preview" not in r
    approved = service.list_requests("D013", status="APPROVED")["requests"][0]
    assert run_tool("D013", "cancel_leave_request", {"request_id": approved["id"]})["error"] == "confirmation_required"
    still = {q["id"]: q["status"] for q in service.list_requests("D024")["requests"]}
    assert still[pending["id"]] == "PENDING"


def test_the_agent_cannot_touch_someone_elses_request():
    rid = draft("D010")
    r = run_tool("D006", "cancel_leave_request", {"request_id": rid})
    assert not r["ok"] and r["error"] == "not_found"


# --- audit and failure handling ------------------------------------------
def test_agent_calls_are_audited_with_the_agent_channel_and_app_calls_are_not():
    run_tool("D010", "get_leave_balance", {})
    service.get_leave_balance("D010")
    rows = audit()
    assert [(r["actor"], r["channel"]) for r in rows] == [("D010", "agent"), ("D010", "app")]
    run_tool("D010", "create_leave_draft", dict(leave_code="CL", start_date="2026-12-02", end_date="2026-12-02"))
    assert audit("action = 'create_leave_draft'")[0]["channel"] == "agent"


def test_the_channel_is_reset_even_when_a_tool_fails(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database exploded with secret details")

    monkeypatch.setattr(service, "get_manager", boom)
    r = run_tool("D010", "get_my_manager")
    assert not r["ok"] and r["error"] == "tool_failed" and "secret" not in r["message"]
    assert service.channel.get() == "app"

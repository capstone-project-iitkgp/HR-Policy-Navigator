"""The MCP server: tools list, calls, identity and the real stdio process."""

import asyncio
import os
import sys
from contextlib import closing
from pathlib import Path

from mcp import Client, StdioServerParameters

from hrms import agent_tools, db, service
from hrms.mcp_server import build_server

CODE_DIR = str(Path(__file__).resolve().parents[2])


def with_client(actor, work, server=None):
    """Run `await work(client)` against an in-process server acting as `actor`."""
    async def go():
        async with Client(server or build_server(actor)) as client:
            return await work(client)

    return asyncio.run(go())


def call(actor, name, arguments=None):
    return with_client(actor, lambda c: c.call_tool(name, arguments or {}))


def test_the_same_eight_tools_with_the_same_schemas_are_listed():
    listed = with_client("D010", lambda c: c.list_tools()).tools
    assert [t.name for t in listed] == list(agent_tools.TOOLS)
    expected = {s["function"]["name"]: s["function"]["parameters"] for s in agent_tools.tool_schemas()}
    assert all(t.input_schema == expected[t.name] for t in listed)
    assert all(t.description for t in listed)


def test_no_tool_can_file_approve_or_pick_an_employee():
    names = {t.name for t in with_client("D010", lambda c: c.list_tools()).tools}
    assert not {"submit_request", "modify_request", "decide_request", "list_pending_approvals"} & names
    for t in with_client("D010", lambda c: c.list_tools()).tools:
        assert t.input_schema.get("additionalProperties") is False
        assert not {"emp_id", "actor_id", "confirmed"} & set(t.input_schema.get("properties", {}))


def test_a_call_returns_the_trimmed_result_as_text_and_structured_data():
    r = call("D010", "get_leave_balance", {"leave_code": "EL"})
    assert not r.is_error
    assert r.structured_content["balances"][0]["available"] == 6.0
    assert '"available": 6.0' in r.content[0].text


def test_the_server_acts_as_the_employee_it_was_started_for():
    mine = call("D010", "get_leave_balance", {"leave_code": "EL"}).structured_content["balances"][0]["available"]
    other = call("D020", "get_leave_balance", {"leave_code": "EL"}).structured_content["balances"][0]["available"]
    assert mine != other


def test_extra_arguments_are_rejected_and_flagged_as_an_error_result():
    r = call("D010", "get_leave_balance", {"emp_id": "D006"})
    assert r.is_error and r.structured_content["error"] == "bad_arguments"


def test_unknown_tool_and_rule_failures_are_error_results_not_crashes():
    assert call("D010", "approve_leave", {}).structured_content["error"] == "unknown_tool"
    r = call("D020", "create_leave_draft", {"leave_code": "CL", "start_date": "2026-12-01", "end_date": "2026-12-01"})
    assert r.is_error and r.structured_content["errors"][0]["code"] == "insufficient_balance"


def test_a_draft_through_mcp_is_only_a_draft_and_is_audited_as_the_agent():
    r = call("D010", "create_leave_draft", {"leave_code": "CL", "start_date": "2026-12-02", "end_date": "2026-12-02"})
    assert not r.is_error and r.structured_content["status"] == "DRAFT"
    saved = next(q for q in service.list_requests("D010")["requests"] if q["id"] == r.structured_content["request_id"])
    assert saved["status"] == "DRAFT"
    with closing(db.get_conn()) as conn:
        row = conn.execute("SELECT actor, channel FROM audit_log WHERE action = 'create_leave_draft'").fetchone()
    assert (row["actor"], row["channel"]) == ("D010", "agent")


def test_the_real_stdio_process_works_end_to_end(tmp_path):
    env = dict(os.environ, HRMS_ACTING_EMPLOYEE="D010", HRMS_DB_PATH=str(tmp_path / "stdio.db"), HRMS_TODAY="2026-11-16")
    params = StdioServerParameters(command=sys.executable, args=["-m", "hrms.mcp_server"], env=env, cwd=CODE_DIR)

    async def work(client):
        tools = await client.list_tools()
        result = await client.call_tool("get_my_manager", {})
        return len(tools.tools), result.structured_content

    count, manager = with_client(None, work, server=params)
    assert count == 8 and manager["manager"]["name"] == "Anita Rao"


def test_the_server_refuses_to_start_without_a_valid_employee(tmp_path):
    import subprocess
    base = dict(os.environ, HRMS_DB_PATH=str(tmp_path / "x.db"))
    base.pop("HRMS_ACTING_EMPLOYEE", None)
    for env, text in ((base, "Set HRMS_ACTING_EMPLOYEE"), (dict(base, HRMS_ACTING_EMPLOYEE="NOPE"), "not an employee")):
        done = subprocess.run([sys.executable, "-m", "hrms.mcp_server"], cwd=CODE_DIR, env=env,
                              capture_output=True, text=True, timeout=60)
        assert done.returncode != 0 and text in done.stderr

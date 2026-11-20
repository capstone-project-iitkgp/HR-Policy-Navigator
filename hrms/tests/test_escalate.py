"""escalate_to_hr: an audited hand-over note with a per-organisation contact; never a model-visible tool."""

from contextlib import closing

from hrms import agent_tools, db, mcp_server, rules, service


def rows(where="action = 'escalate_to_hr'"):
    with closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM audit_log WHERE %s ORDER BY id" % where)]


def test_escalation_writes_one_audit_row_and_returns_its_reference():
    r = service.escalate_to_hr("D010", "refuse_pii", "What is Priya's salary?")
    assert r["ok"] and r["topic"] == "refuse_pii"
    (row,) = rows()
    assert r["reference"] == "ESC-%d" % row["id"] and row["actor"] == "D010" and row["ok"] == 1
    assert "Priya" in row["details"]


def test_agent_channel_is_recorded():
    token = service.channel.set("agent")
    try:
        service.escalate_to_hr("D010", "escalate_to_human", "q")
    finally:
        service.channel.reset(token)
    assert rows()[0]["channel"] == "agent"


def test_summary_is_cut_and_whitespace_collapsed():
    r = service.escalate_to_hr("D010", "t", "a\n\n  b " + "x" * 500)
    assert len(r["summary"]) == service.ESCALATION_SUMMARY_MAX and r["summary"].startswith("a b x")


def test_contact_depends_on_the_employees_organisation():
    with closing(db.get_conn()) as conn:
        orgs = {r["policy_org"]: r["emp_id"] for r in conn.execute("SELECT emp_id, policy_org FROM employees")}
    assert set(orgs) == {"DEF", "BLDEDU"}
    for org, emp in orgs.items():
        assert service.escalate_to_hr(emp, "t", "q")["contact"] == rules.HR_CONTACT[org]


def test_unknown_actor_fails_and_is_audited():
    r = service.escalate_to_hr("NOPE", "t", "q")
    assert not r["ok"] and r["error"] == "unknown_actor" and rows()[0]["ok"] == 0


def test_it_is_not_a_model_visible_tool():
    assert "escalate_to_hr" not in [t["function"]["name"] for t in agent_tools.tool_schemas()]
    assert "escalate_to_hr" not in str(mcp_server.__dict__.get("TOOLS", ""))

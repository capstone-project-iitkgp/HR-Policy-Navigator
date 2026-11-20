import pytest

from hr_navigator.agents.toolbox import HRMS_TOOLS, POLICY_TOOL, ToolBox
from tests.fake_search import CASUAL, CLAIMS, make_search

WHO = "D010"


@pytest.fixture(autouse=True)
def hrms_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HRMS_DB_PATH", str(tmp_path / "hrms_test.db"))
    monkeypatch.setenv("HRMS_TODAY", "2026-11-16")


@pytest.fixture
def box():
    return ToolBox(make_search([CASUAL, CLAIMS]))


def names(schemas):
    return [s["function"]["name"] for s in schemas]


def test_tools_offered_depend_on_route(box):
    assert names(box.schemas("policy")) == [POLICY_TOOL]
    assert set(names(box.schemas("hrms"))) == set(HRMS_TOOLS) and len(HRMS_TOOLS) == 7
    assert names(box.schemas("policy_and_hrms"))[0] == POLICY_TOOL and len(box.schemas("policy_and_hrms")) == 8
    for route in ("clarify", "refuse_injection", "escalate", "out_of_scope"):
        assert box.schemas(route) == []


def test_profile_is_not_offered_but_readable_by_code(box):
    assert "get_my_profile" not in HRMS_TOOLS
    assert box.profile(WHO).get("policy_org")
    assert box.profile("NOBODY") == {}


def test_policy_search_returns_snippets_and_full_passages(box):
    out = box.run(POLICY_TOOL, {"query": "casual leave"}, WHO, route="policy")
    assert out.result["ok"] and out.result["count"] == 1
    assert out.result["passages"][0]["id"] == "z-1"
    assert len(out.result["passages"][0]["text"]) <= 300 and len(out.passages[0].text) > 300


def test_arguments_may_be_a_json_string(box):
    assert box.run(POLICY_TOOL, '{"query": "travel claims"}', WHO).result["passages"][0]["id"] == "z-2"


@pytest.mark.parametrize("args", [{}, {"query": ""}, {"query": 5}, {"query": "x", "k": 3}, "not json", "[1]"])
def test_bad_search_arguments_are_errors_not_exceptions(box, args):
    out = box.run(POLICY_TOOL, args, WHO)
    assert out.result["ok"] is False and out.result["error"] == "bad_arguments" and out.passages == []


def test_empty_search_says_so(box):
    out = box.run(POLICY_TOOL, {"query": "zzzz qqqq"}, WHO)
    assert out.result["ok"] and out.result["count"] == 0 and "No passage" in out.result["message"]


def test_search_that_raises_becomes_an_error():
    def boom(query, k=5, filters=None):
        raise RuntimeError("index missing")
    out = ToolBox(boom).run(POLICY_TOOL, {"query": "leave"}, WHO)
    assert out.result["ok"] is False and out.result["error"] == "search_failed"
    assert "index missing" not in str(out.result)


def test_tool_not_offered_for_the_route_is_refused(box):
    out = box.run("create_leave_draft", {"leave_code": "CL", "start_date": "2026-12-02",
                                         "end_date": "2026-12-02"}, WHO, route="policy")
    assert out.result["error"] == "tool_not_available"
    assert box.run("get_my_profile", {}, WHO, route="hrms").result["error"] == "tool_not_available"


def test_unknown_tool_is_an_error(box):
    assert box.run("delete_everything", {}, WHO).result["error"] == "unknown_tool"


def test_hrms_tool_runs_as_the_actor(box):
    out = box.run("get_leave_balance", {}, WHO, route="hrms")
    assert out.result["ok"] and out.result["balances"]


def test_model_supplied_employee_id_is_ignored(box):
    mine = box.run("get_leave_balance", {}, WHO).result
    sneaky = box.run("get_leave_balance", {"employee_id": "D001", "emp_id": "D001"}, WHO).result
    assert sneaky == mine


def test_bad_hrms_arguments_come_back_as_errors(box):
    out = box.run("get_leave_balance", {"leave_code": "NOPE"}, WHO)
    assert out.result["ok"] is False and out.result["error"] == "bad_arguments"


def test_draft_is_only_a_draft(box):
    out = box.run("create_leave_draft", {"leave_code": "CL", "start_date": "2026-12-02",
                                         "end_date": "2026-12-02"}, WHO, route="hrms")
    assert out.result["ok"] and out.result["status"] == "DRAFT" and "cannot submit" in out.result["next_step"]

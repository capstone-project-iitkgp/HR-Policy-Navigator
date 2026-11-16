"""Check the HRMS eval extension (data/eval/hrms_ext_v1.jsonl) against the seeded HR database.

The gold numbers in each item are not trusted: every `verify` step runs the real tool as the item's
employee and compares the result, and the ideal tool calls are replayed to confirm they end in the
`state_after` the item describes. If the seed data or the rules change, a wrong item fails here.
"""

import json
from contextlib import closing
from pathlib import Path

import pytest

from hrms import agent_tools, db
from hrms.agent_tools import run_tool

EVAL_FILE = Path(__file__).resolve().parents[2] / "data" / "eval" / "hrms_ext_v1.jsonl"
ITEMS = [json.loads(line) for line in EVAL_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]

REQUIRED = ["qid", "category", "type", "difficulty", "acting_employee", "today", "question", "employee_context",
            "answerable", "gold_answer", "gold_facts", "gold_sources", "evidence_quotes", "expected_behaviour",
            "expected_tools", "expected_calls", "must_not", "must_not_tools", "state_after", "verify", "notes",
            "authorship"]
CATEGORIES = {"HRMS Lookup", "HRMS Eligibility & Calculation", "HRMS Actions", "HRMS Safety"}
TYPES = {"db_lookup", "policy_plus_db", "calculation", "eligibility_db", "action_draft", "action_blocked",
         "over_reach", "injection", "ambiguous"}
BEHAVIOURS = {"answer", "draft_for_confirmation", "refuse_pii", "refuse_injection", "escalate_to_human", "clarify"}
ASSISTANT_TOOLS = set(agent_tools.TOOLS) | {"policy_search", "escalate_to_hr"}  # the last two live elsewhere
IDS = [item["qid"] for item in ITEMS]


def at(value, path):
    """Follow 'balances.0.available' into nested dicts and lists."""
    for part in path.split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def snapshot():
    with closing(db.get_conn()) as conn:
        return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM leave_requests")}


def test_the_file_is_well_formed():
    assert len(ITEMS) >= 15 and len(set(IDS)) == len(IDS)
    assert all(qid.startswith("H") and qid[1:].isdigit() for qid in IDS)
    for item in ITEMS:
        assert set(REQUIRED) <= set(item), (item["qid"], set(REQUIRED) - set(item))
        assert item["category"] in CATEGORIES and item["type"] in TYPES, item["qid"]
        assert item["expected_behaviour"] in BEHAVIOURS, item["qid"]
        assert item["difficulty"] in ("easy", "medium", "hard"), item["qid"]
        assert item["today"] == db.DEFAULT_TODAY, item["qid"]  # the data is built for this date
        assert item["gold_answer"].strip() and item["gold_facts"] and item["must_not"], item["qid"]
        assert set(item["expected_tools"]) <= ASSISTANT_TOOLS, item["qid"]
        assert {c["tool"] for c in item["expected_calls"]} <= set(item["expected_tools"]), item["qid"]
        assert set(item["must_not_tools"]) <= set(agent_tools.TOOLS), item["qid"]
        assert not {c["tool"] for c in item["expected_calls"]} & set(item["must_not_tools"]), item["qid"]
        assert len(item["gold_sources"]) == len(item["evidence_quotes"]), item["qid"]
        assert item["verify"], item["qid"]


def test_every_item_says_it_is_an_unreviewed_ai_assisted_draft():
    for item in ITEMS:
        a = item["authorship"]
        assert a["status"] in ("DRAFT", "REVIEWED") and "AI assistance" in a["drafted"]
        if a["status"] == "DRAFT":
            assert a["reviewer"] is None and a["review_date"] is None, item["qid"]
        else:
            assert a["reviewer"] and a["review_date"], item["qid"]


def test_the_set_covers_actions_refusals_and_both_organisations():
    assert {i["type"] for i in ITEMS} >= {"action_draft", "action_blocked", "over_reach", "injection", "ambiguous"}
    assert {i["employee_context"]["org"] for i in ITEMS} == {"DEF", "BLDEDU"}
    assert {i["expected_behaviour"] for i in ITEMS} >= {"draft_for_confirmation", "refuse_pii", "refuse_injection", "clarify"}


def mismatches(item):
    """Run the item's verify steps as its employee; return a description of every value that differs."""
    problems = []
    for step in item["verify"]:
        result = run_tool(item["acting_employee"], step["tool"], step["args"])
        for path, expected in step.get("expect", {}).items():
            try:
                actual = at(result, path)
            except (KeyError, IndexError, ValueError, TypeError):
                actual = "<missing>"
            if actual != expected:
                problems.append("%s %s: %s is %r, expected %r" % (item["qid"], step["tool"], path, actual, expected))
        for path, size in step.get("expect_len", {}).items():
            if len(at(result, path)) != size:
                problems.append("%s %s: %s has %d entries, expected %d" % (
                    item["qid"], step["tool"], path, len(at(result, path)), size))
    return problems


@pytest.mark.parametrize("item", ITEMS, ids=IDS)
def test_gold_values_match_the_database(item):
    assert mismatches(item) == []


@pytest.mark.parametrize("item", ITEMS, ids=IDS)
def test_the_ideal_tool_calls_end_in_the_gold_state(item):
    before = snapshot()
    for step in item["expected_calls"]:
        run_tool(item["acting_employee"], step["tool"], step["args"])
    after = snapshot()
    new = [after[i] for i in sorted(set(after) - set(before))]
    wanted = item["state_after"]["new_requests"]
    assert len(new) == len(wanted), "%s created %d requests, expected %d" % (item["qid"], len(new), len(wanted))
    for row, spec in zip(new, wanted):
        assert {k: row[k] for k in spec} == spec, item["qid"]
        assert row["emp_id"] == item["acting_employee"]
    for rid in item["state_after"].get("unchanged", []):
        assert after[rid] == before[rid], "%s changed request %d" % (item["qid"], rid)
    assert all(after[i] == before[i] for i in before), "%s changed an existing request" % item["qid"]


def test_a_wrong_gold_value_is_caught():
    first = ITEMS[0]  # H001: 6 days of earned leave available
    wrong = dict(first, verify=[dict(first["verify"][0], expect={"balances.0.available": 7.0, "balances.9.x": 1})])
    problems = mismatches(wrong)
    assert len(problems) == 2 and "expected 7.0" in problems[0] and "<missing>" in problems[1]

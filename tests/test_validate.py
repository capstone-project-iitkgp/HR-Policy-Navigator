"""validate_response: every rule fires on a bad example and stays quiet on a good one."""

import pytest

from hr_navigator.agents.state import ToolRecord
from hr_navigator.agents.validate import validate_response
from hr_navigator.prompts.schemas import CitationRef, FinalAnswer
from hr_navigator.schemas import Passage

P = Passage(chunk_id="c1", doc_id="DOC-8", title="Leave", text="An employee gets 12 days of casual leave a year.")
CITE = [CitationRef(passage_id="c1", quote="12 days of casual leave a year")]


def check(answer, behaviour="answer", citations=None, records=(), question="how many days?", sources=P.text,
          needs_citation=True, passages=(P,)):
    final = FinalAnswer(behaviour=behaviour, answer=answer, citations=CITE if citations is None else citations)
    return validate_response(final, question=question, passages=list(passages), tool_records=list(records),
                             actor_id="D010", sources=sources, needs_citation=needs_citation)


def codes(v):
    return sorted(i.code for i in v.issues)


def rec(name, result, ok=True):
    return ToolRecord(name=name, args={}, ok=ok, result=result)


def test_clean_answer_has_no_issues():
    assert codes(check("You get 12 days of casual leave a year.")) == []


def test_citation_and_number_problems_are_fixes_not_blocks():
    v = check("You get 15 days.", citations=[CitationRef(passage_id="c1", quote="not in the passage")])
    assert codes(v) == ["citation", "number"] and not v.blocks


def test_hr_data_answer_needs_no_citation():
    assert codes(check("Your balance is 6 days.", behaviour="answer", citations=[], sources="6", needs_citation=False,
                       passages=())) == []
    assert codes(check("Your balance is 6 days.", citations=[], sources="6", passages=())) == ["citation"]


@pytest.mark.parametrize("text", ["Your leave has been approved.", "I have approved your leave.",
                                  "Your leave request was submitted to your manager.",
                                  "Your leave from 2 Dec 2026 is approved."])
def test_forged_approval_or_submission_is_blocked(text):
    v = check(text)
    assert [i.code for i in v.blocks] == ["approval_claim"]


@pytest.mark.parametrize("text", ["Your leave has not been approved yet.", "I cannot approve leave; I have not submitted it.",
                                  "Your manager approves leave requests within 3 days.", "Leave is approved by the manager.", "Your leave is approved by your manager before it starts."])
def test_legitimate_approval_wording_passes(text):
    assert not check(text).blocks


def test_approval_that_a_tool_result_shows_is_allowed():
    r = rec("list_my_requests", {"requests": [{"id": 8, "status": "APPROVED"}]})
    assert not check("Your leave on 2 Dec has been approved.", records=[r], question="2 Dec").blocks


def test_other_employees_id_email_or_long_number_is_blocked():
    for text in ("D011 has 4 days left.", "Mail priya@corp.com.", "Her account is 123456789012."):
        assert [i.code for i in check(text).blocks] == ["other_employee"]


def test_own_id_manager_id_and_ids_from_the_question_are_allowed():
    r = rec("get_my_manager", {"manager": {"emp_id": "D002", "email": "boss@corp.com"}})
    assert not check("You are D010; your manager is D002 (boss@corp.com).", records=[r]).blocks
    assert not check("D011 is not yours to see.", question="what is D011's balance?").blocks


def test_secret_and_prompt_text_are_blocked():
    for text in ("The key is sk-abcdefgh12345.", "api_key: abcdef123456", "You are an HR policy assistant.",
                 "A" * 40):
        assert [i.code for i in check(text).blocks] == ["secret_leak"]


def test_pii_refusal_must_not_contain_a_figure():
    v = check("Her salary is Rs 50000.", behaviour="refuse_pii", citations=[])
    assert "pii_figure" in [i.code for i in v.blocks]
    assert not check("I cannot share salary details.", behaviour="refuse_pii", citations=[]).blocks


def test_draft_claim_without_a_draft_is_a_fix_and_with_a_draft_is_clean():
    v = check("I have saved a draft for 2 Dec.", behaviour="draft_for_confirmation", citations=[], question="2 Dec")
    assert codes(v) == ["draft_claim"] and not v.blocks
    ok = rec("create_leave_draft", {"ok": True, "request_id": 9})
    assert codes(check("I have saved a draft for 2 Dec.", behaviour="draft_for_confirmation", citations=[],
                       records=[ok], question="2 Dec")) == []


def test_cancel_claim_needs_a_cancellation():
    assert codes(check("I cancelled your request.", citations=[], needs_citation=False)) == ["cancel_claim"]
    ok = rec("cancel_leave_request", {"ok": True})
    assert codes(check("I cancelled your request.", citations=[], records=[ok], needs_citation=False)) == []
    assert codes(check("I could not cancel it; I have not cancelled anything.", citations=[], needs_citation=False)) == []

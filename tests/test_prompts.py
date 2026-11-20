"""Prompt loader, schemas and the citation checker. No network."""

import pytest
from pydantic import ValidationError

from hr_navigator import prompts
from hr_navigator.prompts import render, schemas
from hr_navigator.prompts.schemas import FinalAnswer
from hr_navigator.schemas import Passage

P = [Passage(doc_id="DOC-1", chunk_id="c1", title="Leave", text="Full-time employees get 12 casual leave days per year."),
     Passage(doc_id="DOC-2", chunk_id="c2", title="Travel", text="Claims must be filed within seven (7) calendar days.")]


@pytest.mark.parametrize("name", prompts.NAMES)
def test_every_prompt_loads_is_short_and_has_no_stray_placeholder(name):
    p = prompts.load(name)
    assert p.version >= 1 and len(p.hash) == 12
    assert len(p.text.split()) < 450  # small model, 8192-token window
    with pytest.raises(ValueError, match="no value for"):
        p.render()  # placeholders must be filled


def test_render_fills_all_values():
    out = prompts.load("planner").render(today="2026-11-20", profile="org: DEF", question="How many days?")
    assert "How many days?" in out and "{{" not in out


def test_unknown_prompt_and_version():
    with pytest.raises(ValueError):
        prompts.load("nope")
    with pytest.raises(KeyError):
        prompts.load("planner", version=99)


def test_hash_changes_with_text():
    a = prompts.Prompt("planner", 1, "a")
    assert a.hash != prompts.Prompt("planner", 1, "b").hash
    assert a.hash == prompts.Prompt("planner", 2, "a").hash


def test_plan_schema_rejects_unknown_route_and_long_steps():
    with pytest.raises(ValidationError):
        schemas.Plan(route="chitchat", reason="x")
    with pytest.raises(ValidationError):
        schemas.Plan(route="policy", reason="x", steps=list("abcde"))


def test_format_passages_has_ids_and_empty_marker():
    text = render.format_passages(P)
    assert "[c1] Leave (DOC-1)" in text and "12 casual leave days" in text
    assert "no passages" in render.format_passages([])


def good(quote="12 casual leave days per year"):
    return schemas.FinalAnswer(behaviour="answer", answer="12 days",
                               citations=[schemas.CitationRef(passage_id="c1", quote=quote)])


def test_citation_ok_ignores_case_and_spacing():
    assert render.check_citations(good("12  CASUAL leave days   per year"), P) == []


def test_citation_unknown_passage_and_invented_quote():
    bad_id = schemas.FinalAnswer(behaviour="answer", answer="x",
                                 citations=[schemas.CitationRef(passage_id="zz", quote="12 days")])
    assert "not retrieved" in render.check_citations(bad_id, P)[0]
    assert "not in passage" in render.check_citations(good("a daily allowance of $50"), P)[0]


def test_answer_without_citation_is_a_problem_but_refusal_is_not():
    none = schemas.FinalAnswer(behaviour="answer", answer="12 days")
    assert render.check_citations(none, P)
    refuse = schemas.FinalAnswer(behaviour="refuse_not_in_corpus", answer="The documents do not say.")
    assert render.check_citations(refuse, P) == []


def test_unsupported_numbers_finds_invented_figures_only():
    src = "Casual leave is 12 days a year. Claims within 7 days."
    assert render.unsupported_numbers("You get 12 days, claim within 7.", src, "How many?") == []
    assert render.unsupported_numbers("You get 15 days and $50.", src) == ["15", "50"]


# ---- snap_quotes and number matching (20 Nov, from the first real run) -------

def test_unsupported_numbers_treats_6_and_6_0_as_equal():
    assert render.unsupported_numbers("You have 6 days", '"available": 6.0') == []
    assert render.unsupported_numbers("1000 rupees", "up to 1,000 rupees") == []
    assert render.unsupported_numbers("7 days", '"available": 6.0') == ["7"]


def _passage(text):
    return Passage(doc_id="d", chunk_id="c1", text=text)


def test_snap_quotes_repairs_one_changed_word():
    p = _passage("Intro line. Employees who resign are expected to serve a notice period of one month. Other text.")
    a = FinalAnswer(behaviour="answer", answer="x", citations=[
        {"passage_id": "c1", "quote": "Employees who resign are expected to serve a notice of one month."}])
    fixed, n = render.snap_quotes(a, [p])
    assert n == 1 and render.check_citations(fixed, [p]) == []
    assert fixed.citations[0].quote.startswith("Employees who resign")


def test_snap_quotes_leaves_invented_and_exact_quotes_alone():
    p = _passage("Employees get 12 casual leave days per year.")
    exact = FinalAnswer(behaviour="answer", answer="x", citations=[{"passage_id": "c1", "quote": "12 casual leave days"}])
    invented = FinalAnswer(behaviour="answer", answer="x", citations=[{"passage_id": "c1", "quote": "Everyone gets 40 sick days."}])
    assert render.snap_quotes(exact, [p])[1] == 0
    fixed, n = render.snap_quotes(invented, [p])
    assert n == 0 and fixed.citations[0].quote == "Everyone gets 40 sick days."
    unknown = FinalAnswer(behaviour="answer", answer="x", citations=[{"passage_id": "zz", "quote": "whatever"}])
    assert render.snap_quotes(unknown, [p])[1] == 0

from hr_navigator.agents.budget import CHARS_PER_TOKEN, CONTEXT_LIMIT, OUTPUT_RESERVE, estimate_tokens, fit_context
from hr_navigator.agents.state import STEP_CAP, AgentState, call_key
from hr_navigator.schemas import Passage


def passage(i, n=1000):
    return Passage(doc_id=f"d{i}", chunk_id=f"c{i}", title="T", text="x" * n)


def test_call_key_ignores_argument_order():
    assert call_key("a", {"x": 1, "y": 2}) == call_key("a", {"y": 2, "x": 1})
    assert call_key("a", {"x": 1}) != call_key("a", {"x": 2})


def test_passages_are_unique_by_chunk_and_keep_order():
    s = AgentState(question="q")
    assert s.add_passages([passage(1), passage(2)]) == 2
    assert s.add_passages([passage(2), passage(3)]) == 1
    assert [p.chunk_id for p in s.passages] == ["c1", "c2", "c3"]


def test_repeat_detection_and_records():
    s = AgentState(question="q")
    assert not s.is_repeat("policy_search", {"query": "leave"})
    s.record_tool("policy_search", {"query": "leave"}, {"n": 2})
    assert s.is_repeat("policy_search", {"query": "leave"})
    s.record_tool("policy_search", {"query": "leave"}, {"error": "repeat"}, ok=False, blocked=True)
    assert "blocked" in s.gathered_text()


def test_steps_trace_and_cap():
    s = AgentState(question="q")
    for i in range(STEP_CAP):
        assert not s.at_cap
        s.step(f"thing {i}")
    assert s.at_cap and s.trace[0].startswith("1.")
    s.note("fallback")
    assert s.steps == STEP_CAP and s.trace[-1] == "- fallback"


def test_estimate_is_ceiling_of_chars_over_three():
    assert estimate_tokens("") == 0 and estimate_tokens("abcd") == 2


def total_tokens(fixed, passages, texts):
    return estimate_tokens(fixed + "".join(p.text for p in passages) + "".join(texts))


def test_small_input_is_untouched():
    ps = [passage(1, 300), passage(2, 300)]
    kept, texts, notes = fit_context("fixed", ps, [{"ok": True}])
    assert kept == ps and notes == [] and texts == ['{"ok": true}']


def test_fifty_long_passages_fit_under_the_window():
    fixed = "f" * 6000  # about 2000 tokens of prompt
    ps = [passage(i, 1400) for i in range(50)]
    kept, texts, notes = fit_context(fixed, ps, [{"rows": "r" * 9000}])
    assert 0 < len(kept) < 50
    assert kept[0].chunk_id == "c0"  # best first survives
    assert total_tokens(fixed, kept, texts) <= CONTEXT_LIMIT - OUTPUT_RESERVE
    assert any("trimmed" in n for n in notes) and any("tool results" in n for n in notes)


def test_last_passage_is_shortened_not_lost():
    ps = [passage(1, 1400), passage(2, 1400)]
    fixed = "f" * ((CONTEXT_LIMIT - OUTPUT_RESERVE) * CHARS_PER_TOKEN - 2000)  # about 2000 chars free
    kept, _, _ = fit_context(fixed, ps, [])
    assert [p.chunk_id for p in kept] == ["c1", "c2"]
    assert len(kept[1].text) < 1400 and len(kept[0].text) == 1400


def test_no_room_gives_no_passages():
    fixed = "f" * (CONTEXT_LIMIT * CHARS_PER_TOKEN)
    assert fit_context(fixed, [passage(1)], [])[0] == []

"""Tests for evaluation.baselines with a fake chat(): no network."""

import pytest

from evaluation import baselines, evalset
from hr_navigator.llm.client import SchemaError
from hr_navigator.schemas import ChatResult, Usage

ITEM = {"qid": "Q1", "question": "How many days?", "employee_context": {"name_alias": "EMP-1", "org": "DEF", "role": "Clerk"}}


class FakeChat:
    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []

    def __call__(self, messages, **kw):
        self.calls.append((messages, kw))
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        if isinstance(out, baselines.Reply):
            return ChatResult(text=out.model_dump_json(), parsed=out, usage=Usage(tokens_in=50, tokens_out=20),
                              latency_ms=100, finish_reason="stop")
        return ChatResult(text=out, usage=Usage(tokens_in=50, tokens_out=20), latency_ms=100, finish_reason="stop")


def patch(monkeypatch, *outputs):
    fake = FakeChat(*outputs)
    monkeypatch.setattr(baselines, "chat", fake)
    return fake


def test_registered():
    from evaluation.variants import VARIANTS
    assert {"v0_single", "v0_fewshot", "v0_cot"} <= set(VARIANTS)


def test_user_message_omits_alias_and_has_question():
    msg = baselines.user_message(ITEM)
    assert "EMP-1" not in msg and "org: DEF" in msg and "How many days?" in msg


def test_parse_free_text_splits_quotes():
    answer, cites = baselines.parse_free_text('You get 12 days.\nQUOTE: "12 days per year" (DOC-001)\n')
    assert answer == "You get 12 days."
    assert cites[0].quote == "12 days per year" and cites[0].doc_id == "DOC-001"


def test_single_sets_seed_and_stamps(monkeypatch):
    fake = patch(monkeypatch, "12 days.")
    rec = baselines.v0_single(ITEM, 2)
    assert fake.calls[0][1]["seed"] == 2 and fake.calls[0][1]["temperature"] == 0.2
    assert (rec.tokens_in, rec.tokens_out, rec.steps, rec.seed, rec.latency_s) == (50, 20, 1, 2, 0.1)
    assert rec.prompt_hash


def test_fewshot_uses_schema_and_returns_answer(monkeypatch):
    fake = patch(monkeypatch, baselines.Reply(behaviour="answer", answer="12 days"))
    rec = baselines.v0_fewshot(ITEM, 0)
    assert fake.calls[0][1]["schema"] is baselines.Reply and rec.answer_text == "12 days" and rec.json_valid
    assert len(fake.calls[0][0]) == 1 + 2 * len(baselines.FEWSHOT) + 1


def test_fewshot_schema_failure_keeps_raw_text(monkeypatch):
    bad = ChatResult(text="garbage", usage=Usage(tokens_in=9, tokens_out=3), latency_ms=50, json_valid=False)
    patch(monkeypatch, SchemaError("x", bad))
    rec = baselines.v0_fewshot(ITEM, 0)
    assert rec.json_valid is False and rec.answer_text == "garbage" and rec.tokens_in == 9


def test_cot_makes_two_calls_and_sums_tokens(monkeypatch):
    fake = patch(monkeypatch, "1. asking days", "12 days.")
    rec = baselines.v0_cot(ITEM, 0)
    assert len(fake.calls) == 2 and rec.steps == 2 and rec.tokens_in == 100 and rec.answer_text == "12 days."
    assert fake.calls[1][0][-2]["content"] == "1. asking days"


def test_fewshot_examples_do_not_overlap_eval_questions():
    qs = [i["question"].lower() for i in evalset.load("eval_v1")]
    for q, _ in baselines.FEWSHOT:
        assert not any(q.lower().split("question: ")[1][:40] in e for e in qs)

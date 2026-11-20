"""dev_run with a fake chat: checks the plumbing, not the model."""

import json

from evaluation import dev_run
from hr_navigator.prompts.schemas import Critique, FinalAnswer, Plan
from hr_navigator.schemas import ChatResult, Usage


def fake(plan, final, crit):
    outputs = {"dev_planner": plan, "dev_answer": final, "dev_critic": crit}

    def chat(messages, *, schema=None, tag="", **kw):
        return ChatResult(text="x", parsed=outputs[tag], usage=Usage(tokens_in=100, tokens_out=10))
    return chat


def item(qid="D01"):
    return [json.loads(l) for l in dev_run.DEV.read_text().splitlines() if json.loads(l)["qid"] == qid][0]


def test_dev_set_is_well_formed():
    items = [json.loads(l) for l in dev_run.DEV.read_text().splitlines()]
    assert len(items) == 12 and len({i["qid"] for i in items}) == 12
    assert all(i["split"] == "dev" and i["passages"] for i in items)
    assert not any(i["qid"].startswith("Q") for i in items)  # never mixes with eval_v1


def test_happy_path(monkeypatch):
    it = item("D01")
    quote = "serve a probation period of three months"
    monkeypatch.setattr(dev_run, "chat", fake(
        Plan(route="policy", reason="r", steps=["probation"]),
        FinalAnswer(behaviour="answer", answer="Probation is three months.",
                    citations=[{"passage_id": "c1", "quote": quote}]),
        Critique(verdict="ok")))
    row = dev_run.run_item(it)
    assert row["behaviour_ok"] and row["facts_ok"] and row["route_ok"] and row["citation_problems"] == []
    assert row["tokens"] == 330 and set(row["prompt_hashes"]) == {"planner", "answer", "critic"}


def test_invented_quote_is_reported(monkeypatch):
    monkeypatch.setattr(dev_run, "chat", fake(
        Plan(route="policy", reason="r"),
        FinalAnswer(behaviour="answer", answer="Six months.", citations=[{"passage_id": "c1", "quote": "six months of probation"}]),
        Critique(verdict="revise", issues=["number not in passages"])))
    row = dev_run.run_item(item("D01"))
    assert row["citation_problems"] and row["facts_ok"] is False and row["critic"] == "revise"


def test_exception_becomes_error_row(monkeypatch):
    def boom(*a, **k):
        raise ValueError("bad")
    monkeypatch.setattr(dev_run, "chat", boom)
    assert dev_run.run_item(item("D02"))["error"].startswith("ValueError")

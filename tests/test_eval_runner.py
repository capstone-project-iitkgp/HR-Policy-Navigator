"""Tests for evaluation.run_eval: resume, error records, stamps, frozen check."""

import json

import pytest

from evaluation import evalset, run_eval
from evaluation.records import RunRecord
from evaluation.variants import VARIANTS, register
from hr_navigator.llm.http import LLMCapacityError


def lines(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_demo_run_writes_all_records_with_stamps(tmp_path):
    out = tmp_path / "r.jsonl"
    counts = run_eval.run("demo", "eval_v1", 2, out, limit=5, log=lambda m: None)
    rows = lines(out)
    assert counts["written"] == 10 and len(rows) == 10
    assert {r["repeat"] for r in rows} == {0, 1}
    assert rows[0]["eval_sha"] == evalset.sha256("eval_v1") and rows[0]["date"]


def test_resume_skips_done_and_does_not_duplicate(tmp_path):
    out = tmp_path / "r.jsonl"
    run_eval.run("demo", "eval_v1", 1, out, limit=3, log=lambda m: None)
    counts = run_eval.run("demo", "eval_v1", 1, out, limit=5, log=lambda m: None)
    assert counts == {"written": 2, "skipped": 3, "errors": 0}
    keys = [(r["qid"], r["repeat"]) for r in lines(out)]
    assert len(keys) == len(set(keys)) == 5


def test_exception_becomes_error_record_and_run_continues(tmp_path):
    @register("boom_once")
    def boom(item, repeat):
        if item["qid"] == "Q001":
            raise ValueError("bad json")
        return RunRecord(qid=item["qid"], variant="boom_once", answer_text="x")
    try:
        out = tmp_path / "r.jsonl"
        counts = run_eval.run("boom_once", "eval_v1", 1, out, limit=3, log=lambda m: None)
    finally:
        del VARIANTS["boom_once"]
    rows = lines(out)
    assert counts["errors"] == 1 and len(rows) == 3
    assert rows[0]["error"].startswith("ValueError") and rows[1]["error"] is None


def test_capacity_error_stops_run_but_keeps_finished_records(tmp_path):
    @register("cap")
    def cap(item, repeat):
        if item["qid"] == "Q003":
            raise LLMCapacityError(503, "busy")
        return RunRecord(qid=item["qid"], variant="cap")
    try:
        out = tmp_path / "r.jsonl"
        with pytest.raises(SystemExit, match="at capacity"):
            run_eval.run("cap", "eval_v1", 1, out, limit=5, log=lambda m: None)
    finally:
        del VARIANTS["cap"]
    assert len(lines(out)) == 2


def test_unknown_variant_exits(tmp_path):
    with pytest.raises(SystemExit, match="unknown variant"):
        run_eval.run("nope", "eval_v1", 1, tmp_path / "r.jsonl")


def test_official_requires_matching_freeze(tmp_path, monkeypatch):
    monkeypatch.setattr(evalset, "EVAL_DIR", tmp_path)
    (tmp_path / "eval_v1.jsonl").write_text('{"qid": "Q1", "type": "lookup", "gold_answer": "a"}\n')
    with pytest.raises(SystemExit, match="freeze"):
        run_eval.run("demo", "eval_v1", 1, tmp_path / "o.jsonl", official=True)
    (tmp_path / "eval_v1.sha256").write_text(evalset.sha256("eval_v1") + "  eval_v1.jsonl\n")
    assert run_eval.run("demo", "eval_v1", 1, tmp_path / "o.jsonl", official=True,
                        log=lambda m: None)["written"] == 1
    (tmp_path / "eval_v1.jsonl").write_text('{"qid": "Q1", "type": "lookup", "gold_answer": "changed"}\n')
    with pytest.raises(SystemExit, match="freeze"):
        run_eval.run("demo", "eval_v1", 1, tmp_path / "o2.jsonl", official=True)


def test_mock_endpoint_tags_records(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_BASE_URL", "http://127.0.0.1:8099/v1")
    out = tmp_path / "r.jsonl"
    run_eval.run("demo", "eval_v1", 1, out, limit=1, log=lambda m: None)
    assert lines(out)[0]["tag"] == "mock"

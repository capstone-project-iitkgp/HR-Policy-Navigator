import importlib.util
from pathlib import Path

import pytest

from hr_navigator.agents import orchestrator
from hr_navigator.llm.config import Endpoint
from hr_navigator.prompts.schemas import Critique
from tests.test_orchestrator import PLAN, SEARCH, Script, answer

spec = importlib.util.spec_from_file_location("ask", Path(__file__).resolve().parents[1] / "scripts/ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def test_dev_item_runs_end_to_end_with_trace(monkeypatch, capsys):
    monkeypatch.setattr(ask.config, "chat_endpoint", lambda: Endpoint("https://real.example/v1", "Qwen/Qwen3-8B", "k"))
    item = ask.json.loads(ask.DEV.read_text().splitlines()[0])
    passage = item["passages"][0]
    quote = passage["text"][:40]
    final = answer(answer="Yes.", citations=[{"passage_id": passage["chunk_id"], "quote": quote}])
    monkeypatch.setattr(orchestrator, "chat", Script(PLAN, SEARCH, "DONE", final, Critique(verdict="ok")))
    assert ask.main(["--dev", item["qid"], "--llm"]) == 0
    out = capsys.readouterr().out
    assert "passages of the dev item" in out and "--- steps" in out and "policy_search" in out and "Yes." in out


def test_no_question_is_a_clear_error(monkeypatch):
    with pytest.raises(SystemExit, match="give a question"):
        ask.main([])


def test_unknown_dev_item(monkeypatch):
    with pytest.raises(SystemExit, match="no dev item"):
        ask.main(["--dev", "D99"])

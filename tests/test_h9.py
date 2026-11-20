import csv
import json
import os

import pytest

from evaluation import agent_variants, h9_report, h9_run, run_eval
from evaluation.variants import VARIANTS
from hr_navigator.agents import orchestrator
from hr_navigator.prompts.schemas import Critique, FinalAnswer, Plan
from hrms import db, service
from tests.fake_search import CASUAL, CLAIMS, make_search
from tests.test_agent_variants import ITEM, use
from tests.test_orchestrator import PLAN, REFUSE, SEARCH, answer, tool_call


@pytest.fixture(autouse=True)
def search(monkeypatch, tmp_path):
    monkeypatch.setenv("HRMS_DB_PATH", str(tmp_path / "h.db"))
    monkeypatch.delenv("HRMS_TODAY", raising=False)
    agent_variants.set_search(make_search([CASUAL, CLAIMS]))
    yield
    agent_variants.set_search(None)


# --- plan -----------------------------------------------------------------------------------
def test_plan_main_stage_is_two_variants_on_both_sets_with_three_repeats():
    jobs = h9_run.plan(["main"])
    assert [(j["variant"], j["eval"], j["repeats"]) for j in jobs] == [
        ("v2_rag", "eval_v1", 3), ("v2_rag", "hrms_ext_v1", 3), ("v3_agent", "eval_v1", 3), ("v3_agent", "hrms_ext_v1", 3)]
    assert jobs[2]["questions"] == 240 and jobs[3]["questions"] == 60


def test_plan_ablations_use_one_repeat_on_eval_v1_unless_overridden():
    jobs = h9_run.plan(["ablate"])
    assert {j["eval"] for j in jobs} == {"eval_v1"} and {j["repeats"] for j in jobs} == {1}
    assert {j["repeats"] for j in h9_run.plan(["ablate"], ablation_repeats=2)} == {2}
    assert [j["variant"] for j in h9_run.plan(["main"], variants=("v3_agent",), evals=("eval_v1",))] == ["v3_agent"]


def test_every_planned_variant_is_registered():
    assert {j["variant"] for j in h9_run.plan(["main", "ablate"])} <= set(VARIANTS)


def test_dry_run_prints_the_plan_and_runs_nothing(capsys):
    assert h9_run.main(["--stage", "all", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "v3_agent" in out and "UNVERIFIED" in out and "LLM_CACHE" not in os.environ


def test_preflight_names_each_problem(monkeypatch):
    agent_variants.set_search(None)
    monkeypatch.setattr(h9_run.evalset, "is_frozen", lambda name: name != "hrms_ext_v1")
    monkeypatch.setattr(h9_run.run_eval, "is_mock", lambda: False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr("importlib.import_module", lambda name: (_ for _ in ()).throw(ImportError(name)))
    problems = h9_run.preflight(False)
    text = " ".join(problems)
    assert "hrms_ext_v1 is not frozen" in text and "retrieval" in text and "API_KEY" in text


def test_main_refuses_to_start_when_preflight_fails(monkeypatch, capsys):
    monkeypatch.setattr(h9_run, "preflight", lambda mock: ["retrieval missing"])
    monkeypatch.setenv("LLM_CACHE", "1")  # main() sets it to 0; monkeypatch puts the old state back
    assert h9_run.main(["--stage", "main"]) == 2
    assert "retrieval missing" in capsys.readouterr().out
    assert os.environ["LLM_CACHE"] == "0"  # real timings are the default


# --- run records -----------------------------------------------------------------------------
def test_agent_facts_are_in_the_record(monkeypatch):
    use(monkeypatch, PLAN, SEARCH, "DONE", answer(), Critique(verdict="ok"))
    rec = VARIANTS["v3_agent"](ITEM, 0)
    f = rec.agent
    assert f["behaviour"] == "answer" and f["status"] == "answered" and f["blocked"] is False
    assert f["llm_calls"] == 5 and f["cache_hits"] == 0 and f["critic_verdict"] == "ok" and f["revisions"] == 0


ACTING = {**ITEM, "acting_employee": "D010", "today": "2026-11-16"}
FORGED = FinalAnswer(behaviour="clarify", answer="Your leave has been approved.", citations=[])


def test_refusal_is_recorded_as_escalated(monkeypatch):
    use(monkeypatch, PLAN, SEARCH, "DONE", REFUSE, Critique(verdict="ok"))
    rec = VARIANTS["v3_agent"](ACTING, 0)
    assert rec.agent["escalated"] is True and rec.agent["behaviour"] == "refuse_not_in_corpus"


def test_blocked_answer_is_recorded_and_noblock_only_reports(monkeypatch):
    use(monkeypatch, Plan(route="out_of_scope", reason="r"), FORGED, Critique(verdict="ok"))
    rec = VARIANTS["v3_agent"](ACTING, 0)
    assert rec.agent["blocked"] is True and "approval_claim" in rec.agent["issues"]
    assert "cannot confirm or record an approval" in rec.answer_text
    use(monkeypatch, Plan(route="out_of_scope", reason="r"), FORGED, Critique(verdict="ok"))
    rec2 = VARIANTS["v3_agent_noblock"](ACTING, 0)
    assert rec2.agent["blocked"] is False and rec2.agent["report_only_block"] is True
    assert rec2.answer_text.startswith("Your leave has been approved") and rec2.prompt_hash != rec.prompt_hash


def test_ablation_variants_change_the_run_hash_and_options(monkeypatch):
    hashes = {}
    for name in ("v3_agent", "v3_agent_lenient", "v3_agent_noblock", "v3_agent_revise"):
        use(monkeypatch, PLAN, SEARCH, "DONE", answer(), Critique(verdict="ok"))
        hashes[name] = VARIANTS[name](ITEM, 0).prompt_hash
    assert len(set(hashes.values())) == 4


def test_revise_variant_revises_when_the_critic_asks(monkeypatch):
    script = use(monkeypatch, PLAN, SEARCH, "DONE", answer(), Critique(verdict="revise", issues=["be precise"]),
                 answer(answer="You get 12 casual leave days a year (Zeta policy)."))
    rec = VARIANTS["v3_agent_revise"](ITEM, 0)
    assert rec.agent["revisions"] == 1 and rec.agent["critic_verdict"] == "revise" and len(script.calls) == 6
    assert "Zeta policy" in rec.answer_text


def test_cache_hits_are_counted(monkeypatch):
    use(monkeypatch, PLAN, SEARCH, "DONE", answer(), Critique(verdict="ok"))
    real = orchestrator.chat

    def cached(*a, **k):
        res = real(*a, **k)
        res.cached = True
        return res
    monkeypatch.setattr(orchestrator, "chat", cached)
    assert VARIANTS["v3_agent"](ITEM, 0).agent["cache_hits"] == 5


# --- the HRMS database is fresh for every item ----------------------------------------------------
HITEM = {"qid": "H901", "question": "Apply for 1 day of casual leave on 2026-12-02", "acting_employee": "D010",
         "today": "2026-11-16", "employee_context": {}}


def draft_count():
    return len(service.list_requests("D010", status="DRAFT")["requests"])


def test_isolated_hrms_discards_changes_and_restores_the_environment(tmp_path):
    before = os.environ["HRMS_DB_PATH"]
    with agent_variants.isolated_hrms("2026-11-16") as path:
        assert os.environ["HRMS_DB_PATH"] == str(path) and db.today().isoformat() == "2026-11-16"
        base = draft_count()  # the seeded data already holds some drafts
        res = service.create_leave_draft("D010", "CL", "2026-12-02", "2026-12-02")
        assert res["ok"] and draft_count() == base + 1
    assert os.environ["HRMS_DB_PATH"] == before and "HRMS_TODAY" not in os.environ and not path.exists()
    with agent_variants.isolated_hrms():
        assert draft_count() == base  # the next run starts clean


def test_hrms_item_run_leaves_no_draft_behind(monkeypatch):
    use(monkeypatch, Plan(route="hrms", reason="r"),
        tool_call("create_leave_draft", {"leave_code": "CL", "start_date": "2026-12-02", "end_date": "2026-12-02"}),
        "DONE", FinalAnswer(behaviour="draft_for_confirmation", answer="I made a draft; please confirm."),
        Critique(verdict="ok"))
    with agent_variants.isolated_hrms():
        base = draft_count()
    rec = VARIANTS["v3_agent"](HITEM, 0)
    assert any(t.name == "create_leave_draft" and t.error is None for t in rec.tool_calls)  # it did draft
    with agent_variants.isolated_hrms():
        assert draft_count() == base
    assert "HRMS_TODAY" not in os.environ


# --- the cache switch -------------------------------------------------------------------------------
def test_llm_cache_switch_turns_the_disk_cache_off(monkeypatch, tmp_path):
    import httpx
    from hr_navigator.llm import client, config, http

    monkeypatch.setattr(client, "CACHE_DIR", tmp_path / "c")
    monkeypatch.setenv("LLM_CACHE", "0")
    body = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1}}
    ep = config.Endpoint(base_url="http://x/v1", model="m", api_key="k")
    http_client = http.make_client(ep, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    r1 = client.chat([{"role": "user", "content": "q"}], endpoint=ep, client=http_client)
    r2 = client.chat([{"role": "user", "content": "q"}], endpoint=ep, client=http_client)
    assert not r1.cached and not r2.cached and not (tmp_path / "c").exists()


# --- the report -------------------------------------------------------------------------------------
def demo_run(tmp_path, variant, drop=()):
    """Records from the gold-playback 'demo' variant, relabelled; answers of `drop` qids blanked."""
    out = tmp_path / "runs" / f"{variant}__eval_v1.jsonl"
    run_eval.run("demo", "eval_v1", 1, out, log=lambda *_: None)
    lines = []
    for line in out.read_text().splitlines():
        r = json.loads(line)
        r["variant"] = variant
        if r["qid"] in drop:
            r["answer_text"], r["citations"] = "I do not know.", []
        r["agent"] = {"behaviour": "answer", "status": "answered", "issues": [], "blocked": False,
                      "report_only_block": False, "escalated": False, "revisions": 0, "critic_verdict": "ok",
                      "llm_calls": 4, "cache_hits": 0, "llm_ms": 1000}
        lines.append(json.dumps(r))
    out.write_text("\n".join(lines) + "\n")
    out.with_name(out.name + ".bak").write_text("")  # a stray file must not break loading
    return out


def test_report_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHAT_BASE_URL", "http://127.0.0.1:1/v1")  # demo records get tagged mock: expected
    first = [q["qid"] for q in h9_report.score.load_jsonl(h9_report.evalset.path("eval_v1"))][:30]
    demo_run(tmp_path, "v2_rag", drop=first)
    demo_run(tmp_path, "v3_agent")
    assert h9_report.main(["--runs", str(tmp_path / "runs"), "--no-baselines"]) == 0
    out = capsys.readouterr().out
    rep = tmp_path / "runs" / "report"
    text = (rep / "report.md").read_text()
    assert "V2 RAG" in text and "V3 agent" in text and "MOCK RUN" in text
    assert "V3 (agent) passes more questions than V2 (RAG)" in text  # V2 lost 30 questions V3 kept
    assert "Latency and cost" in text and "Failure labels" in text and "needs_human" in out
    sheet = list(csv.DictReader(open(rep / "taxonomy_sheet.csv")))
    assert sheet and {r["variant"] for r in sheet} == {"v2_rag", "v3_agent"} and all(r["human_label"] == "" for r in sheet)
    assert (rep / "scores_v3_agent__eval_v1.csv").is_file() and (rep / "needs_human.csv").is_file()


def test_report_flags_replayed_latency(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_BASE_URL", "http://127.0.0.1:1/v1")
    out = demo_run(tmp_path, "v3_agent")
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    for r in lines:
        r["agent"]["cache_hits"] = 4
    out.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    rows, reviews, warns = h9_report.score_all(h9_report.load_records(tmp_path / "runs", False))
    text = h9_report.build(rows, reviews, warns, [])
    assert "replayed from the cache" in text


def test_report_with_no_records_says_what_to_run(tmp_path, capsys):
    assert h9_report.main(["--runs", str(tmp_path / "none"), "--no-baselines"]) == 1
    assert "h9_run" in capsys.readouterr().out


def test_verdict_needs_both_variants():
    assert "Not decided" in h9_report.verdict([])

"""Dev loop for the H4 prompts: planner -> answer -> critic on data/dev/dev_v1.jsonl.

    python -m evaluation.dev_run [--out FILE] [--limit N] [--agent]

Passages come with each dev item (no retrieval, no tools). This is for reading outputs and
fixing prompts, NOT a result: the items are few and were written alongside the prompts.
The frozen eval sets are never used here.
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

from hr_navigator import prompts
from hr_navigator.llm import client
from hr_navigator.prompts import render
from hr_navigator.prompts.schemas import Critique, FinalAnswer, Plan
from hr_navigator.schemas import Passage

DEV = Path(__file__).resolve().parents[1] / "data/dev/dev_v1.jsonl"
chat = client.chat  # tests replace this
SYSTEM = "You are a careful assistant. Follow the instructions exactly."


def profile_text(item: dict) -> str:
    return "; ".join(f"{k}: {v}" for k, v in item["employee_context"].items())


def run_item(item: dict, today: str = "2026-11-20") -> dict:
    passages = [Passage(**p) for p in item["passages"]]
    profile, tokens = profile_text(item), 0
    out = {"qid": item["qid"], "expected": item["expected_behaviour"], "expected_route": item["expected_route"]}

    def call(name, schema, **values):
        nonlocal tokens
        p = prompts.load(name)
        res = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": p.render(**values)}],
                   schema=schema, max_tokens=500, tag=f"dev_{name}", temperature=0.2, seed=0)
        tokens += res.usage.tokens_in + res.usage.tokens_out
        out.setdefault("prompt_hashes", {})[name] = p.hash
        out.setdefault("prompt_tokens", {})[name] = res.usage.tokens_in
        return res.parsed

    try:
        plan = call("planner", Plan, today=today, profile=profile, question=item["question"])
        out["route"], out["route_ok"] = plan.route, plan.route.startswith(item["expected_route"]) or (
            item["expected_route"] == "policy" and plan.route == "policy_and_hrms")
        shown = render.format_passages(passages)
        final = call("answer", FinalAnswer, today=today, profile=profile, plan=plan.model_dump_json(),
                     passages=shown, tool_results="(none)", question=item["question"])
        out.update(behaviour=final.behaviour, answer=final.answer, behaviour_ok=final.behaviour == item["expected_behaviour"],
                   citation_problems=render.check_citations(final, passages),
                   unsupported_numbers=render.unsupported_numbers(
                       final.answer, shown, item["question"], profile))
        low = final.answer.lower()
        out["facts_ok"] = all(m.lower() in low for m in item["must_include"])
        crit = call("critic", Critique, behaviour=final.behaviour, profile=profile, question=item["question"], passages=shown,
                    tool_results="(none)", draft=final.answer)
        out.update(critic=crit.verdict, critic_issues=crit.issues)
    except client.SchemaError as exc:
        out["error"] = f"schema: {str(exc)[:200]}"
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    out["tokens"] = tokens
    return out


def run_item_agent(item: dict, today: str = "2026-11-20", tracer=None) -> dict:
    """The full loop on a dev item. The item's own passages are the search result (every query gets
    all of them, in order), so this tests the loop and the prompts, not retrieval."""
    from hr_navigator.agents.orchestrator import Agent
    from hr_navigator.agents.toolbox import ToolBox
    from hr_navigator.schemas import AgentRequest

    passages = [Passage(**p) for p in item["passages"]]
    agent = Agent(ToolBox(lambda query, k=5, filters=None: passages), tracer=tracer)
    out = {"qid": item["qid"], "expected": item["expected_behaviour"], "expected_route": item["expected_route"]}
    try:
        resp, state = agent.run_with_state(AgentRequest(question=item["question"], session_id="", employee_id=None),
                                           seed=0, profile=dict(item["employee_context"], today=today))
        route = state.plan.route if state.plan else ""
        shown = render.format_passages(state.passages)
        out.update(route=route, route_ok=route.startswith(item["expected_route"]) or (
                       item["expected_route"] == "policy" and route == "policy_and_hrms"),
                   behaviour=state.final_behaviour, answer=resp.answer, status=resp.status,
                   behaviour_ok=state.final_behaviour == item["expected_behaviour"],
                   facts_ok=all(m.lower() in resp.answer.lower() for m in item["must_include"]),
                   citation_problems=[] if resp.citations or state.final_behaviour != "answer" else ["no citations"],
                   unsupported_numbers=render.unsupported_numbers(resp.answer, shown, item["question"]),
                   steps=state.steps, tool_calls=[t["name"] for t in resp.tool_calls],
                   trace=[s["note"] for s in resp.steps], tokens=resp.usage.tokens_in + resp.usage.tokens_out)
        critic = [s["note"] for s in resp.steps if "critic ->" in s["note"]]
        out["critic"] = critic[0].split("-> ")[1][:6] if critic else "-"
        if tracer is not None:
            out["trace_id"] = tracer.last_trace_id
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--agent", action="store_true", help="run the full H5 loop instead of planner->answer->critic")
    ap.add_argument("--trace", action="store_true", help="with --agent: write one trace per item to .cache/traces")
    args = ap.parse_args(argv)
    items = [json.loads(l) for l in DEV.read_text().splitlines() if l.strip()][: args.limit]
    out = Path(args.out or f"evaluation/results/dev_{'h5' if args.agent else 'h4'}_{datetime.date.today()}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    tracer = None
    if args.trace:
        if not args.agent:
            raise SystemExit("--trace needs --agent")
        from hr_navigator.observability import tracing
        tracer = tracing.configure(enabled=True)
    rows = []
    with out.open("w", encoding="utf-8") as f:
        for item in items:
            row = run_item_agent(item, tracer=tracer) if args.agent else run_item(item)
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{row['qid']} exp={row['expected']:<20} got={row.get('behaviour', 'ERROR'):<20} "
                  f"route={'ok' if row.get('route_ok') else 'X'} facts={'ok' if row.get('facts_ok') else 'X'} "
                  f"cites={'ok' if not row.get('citation_problems', ['x']) else 'X'} nums={'ok' if not row.get('unsupported_numbers', ['x']) else 'X'} critic={row.get('critic', '-')}"
                  + (f"  {row['error']}" if row.get("error") else ""))
    n = len(rows)
    ok = sum(1 for r in rows if r.get("behaviour_ok"))
    print(f"\nbehaviour {ok}/{n}; route {sum(1 for r in rows if r.get('route_ok'))}/{n}; "
          f"citation problems in {sum(1 for r in rows if r.get('citation_problems'))}; errors {sum(1 for r in rows if r.get('error'))}")
    if tracer is not None:
        from hr_navigator.observability import summary
        traces = summary.by_trace(s for s in summary.load(tracer.sink.directory) if s["trace_id"] in {r.get("trace_id") for r in rows})
        sums = [summary.summarize(t) for t in traces.values()]
        if sums:
            print(f"traces {len(sums)} in {tracer.sink.directory}: mean latency {sum(x['latency_ms'] for x in sums) / len(sums) / 1000:.1f} s, "
                  f"mean tokens {sum(x['tokens_in'] + x['tokens_out'] for x in sums) // len(sums)}, "
                  f"model calls {sum(x['llm_calls'] for x in sums)}, error spans {sum(x['errors'] for x in sums)}")
    print(f"wrote {out}. Read the answers: this is a dev loop, not a result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

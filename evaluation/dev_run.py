"""Dev loop for the H4 prompts: planner -> answer -> critic on data/dev/dev_v1.jsonl.

    python -m evaluation.dev_run [--out FILE] [--limit N]

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
                   citation_problems=render.check_citations(final, passages))
        low = final.answer.lower()
        out["facts_ok"] = all(m.lower() in low for m in item["must_include"])
        crit = call("critic", Critique, profile=profile, question=item["question"], passages=shown,
                    tool_results="(none)", draft=final.answer)
        out.update(critic=crit.verdict, critic_issues=crit.issues)
    except client.SchemaError as exc:
        out["error"] = f"schema: {str(exc)[:200]}"
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    out["tokens"] = tokens
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=f"evaluation/results/dev_h4_{datetime.date.today()}.jsonl")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    items = [json.loads(l) for l in DEV.read_text().splitlines() if l.strip()][: args.limit]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with out.open("w", encoding="utf-8") as f:
        for item in items:
            row = run_item(item)
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{row['qid']} exp={row['expected']:<20} got={row.get('behaviour', 'ERROR'):<20} "
                  f"route={'ok' if row.get('route_ok') else 'X'} facts={'ok' if row.get('facts_ok') else 'X'} "
                  f"cites={'ok' if not row.get('citation_problems', ['x']) else 'X'} critic={row.get('critic', '-')}"
                  + (f"  {row['error']}" if row.get("error") else ""))
    n = len(rows)
    ok = sum(1 for r in rows if r.get("behaviour_ok"))
    print(f"\nbehaviour {ok}/{n}; route {sum(1 for r in rows if r.get('route_ok'))}/{n}; "
          f"citation problems in {sum(1 for r in rows if r.get('citation_problems'))}; errors {sum(1 for r in rows if r.get('error'))}")
    print(f"wrote {out}. Read the answers: this is a dev loop, not a result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""H9: turn the run files into the tables for the report.

    python -m evaluation.h9_report                       # reads evaluation/results/h9 and the H3 baselines
    python -m evaluation.h9_report --runs DIR --out DIR2 --no-baselines

Writes report.md (all tables), scores_<variant>__<eval>.csv, needs_human.csv and taxonomy_sheet.csv
(one row per failed question at repeat 0, with the scorer's suggested label and an empty column for
yours) into the output folder (default: <runs>/report). Rule-based only; no model is called.

What the tables are, and are not:
- Pass rates come from score.py, which was tuned once on the H3 baselines and must not be tuned
  again on the frozen set. Strict pass is the number to report; lenient is the upper bound.
- Failure labels are SUGGESTIONS; a human confirms them in taxonomy_sheet.csv before the report
  quotes them.
- Latency is only real when the records show no cache hits (the cost table says so).
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from evaluation import evalset, score
from evaluation.h9_run import OUT_DIR
from evaluation.run_eval import RESULTS_DIR

BASELINES = ("h3_v0_single.jsonl", "h3_v0_fewshot.jsonl", "h3_v0_cot.jsonl")
ORDER = ["v0_single", "v0_fewshot", "v0_cot", "v2_rag", "v3_agent", "v3_agent_nocritic", "v3_agent_lenient",
         "v3_agent_noblock", "v3_agent_revise"]
COMPARISONS = [("v0_cot", "v2_rag"), ("v2_rag", "v3_agent"), ("v3_agent", "v3_agent_nocritic"),
               ("v3_agent", "v3_agent_lenient"), ("v3_agent", "v3_agent_noblock"), ("v3_agent", "v3_agent_revise")]
LABELS = {"v0_single": "V0 single prompt", "v0_fewshot": "V0 few-shot", "v0_cot": "V0 chain of thought",
          "v2_rag": "V2 RAG", "v3_agent": "V3 agent (critic advisory)", "v3_agent_nocritic": "V3 without critic",
          "v3_agent_lenient": "V3 lenient citations", "v3_agent_noblock": "V3 no blocking",
          "v3_agent_revise": "V3 critic revises"}


def load_records(runs: Path, baselines: bool) -> list[dict]:
    files = sorted(runs.glob("*__*.jsonl")) if runs.is_dir() else []
    if baselines:
        files += [RESULTS_DIR / b for b in BASELINES if (RESULTS_DIR / b).is_file()]
    recs = []
    for f in files:
        for r in score.load_jsonl(f):
            r.setdefault("eval_name", f.stem.split("__")[1] if "__" in f.stem else "eval_v1")
            recs.append(r)
    return recs


def score_all(records: list[dict]) -> tuple[list[dict], list[list], list[str]]:
    """Rows (with the agent facts flattened in as a_*), needs-human reviews, warnings."""
    docs, texts = score.load_sources()
    evals = {n: {q["qid"]: q for q in score.load_jsonl(evalset.path(n))} for n in evalset.NAMES}
    rows, reviews, warns = [], [], []
    for name in evalset.NAMES:
        subset = [r for r in records if r.get("eval_name") == name]
        warns += score.run_warnings(subset, name) if subset else []
    for rec in records:
        q = evals.get(rec.get("eval_name"), {}).get(rec.get("qid"))
        if q is None:
            continue
        row, reasons = score.score(q, rec, docs, texts)
        row["eval"] = rec["eval_name"]
        row["tokens_in"], row["tokens_out"] = rec.get("tokens_in") or 0, rec.get("tokens_out") or 0
        for k, v in (rec.get("agent") or {}).items():
            row["a_" + k] = "+".join(map(str, v)) if isinstance(v, list) else v
        rows.append(row)
        if reasons:
            reviews.append([q["qid"], rec.get("variant"), rec.get("repeat", 0), ";".join(reasons),
                            (rec.get("answer_text") or "")[:600]])
    return rows, reviews, sorted(set(warns))


def ordered(rows: list[dict]) -> list[str]:
    seen = {r["variant"] for r in rows}
    return [v for v in ORDER if v in seen] + sorted(seen - set(ORDER))


def of(rows, variant, eval_name=None):
    return [r for r in rows if r["variant"] == variant and (eval_name is None or r["eval"] == eval_name)]


def pct(x) -> str:
    return f"{x:.2f}"


def table(head: list[str], body: list[list]) -> str:
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
                     + ["| " + " | ".join(str(c) for c in r) + " |" for r in body]) + "\n"


def headline(rows, eval_name) -> str:
    body = []
    for v in ordered(rows):
        rs = of(rows, v, eval_name)
        if not rs:
            continue
        k, n = sum(r["pass"] for r in rs), len(rs)
        p, lo, hi = score.wilson(k, n)
        ans = [r for r in rs if "any@5" in r]
        reps = defaultdict(list)
        for r in rs:
            reps[r["repeat"]].append(r["pass"])
        sd = statistics.stdev([statistics.mean(x) for x in reps.values()]) if len(reps) > 1 else float("nan")
        body.append([LABELS.get(v, v), n, f"{pct(p)} ({pct(lo)}-{pct(hi)})", pct(score.mean([r["pass_lenient"] for r in rs])),
                     "n/a" if sd != sd else f"{sd:.3f}",
                     pct(score.mean([r["any@5"] for r in ans])) if ans else "n/a",
                     pct(score.mean([r["mrr"] for r in ans])) if ans else "n/a"])
    return table(["Variant", "runs", "Pass strict (95% CI)", "Pass lenient", "SD across repeats", "any@5", "MRR"], body)


def by_type(rows, eval_name) -> str:
    variants = [v for v in ordered(rows) if of(rows, v, eval_name)]
    types = sorted({r["type"] for r in rows if r["eval"] == eval_name})
    body = []
    for t in types:
        line = [t]
        for v in variants:
            rs = [r for r in of(rows, v, eval_name) if r["type"] == t]
            line.append(f"{sum(r['pass'] for r in rs)}/{len(rs)}" if rs else "-")
        body.append(line)
    return table(["type", *variants], body)


def taxonomy(rows, eval_name) -> str:
    body = []
    for v in ordered(rows):
        rs = of(rows, v, eval_name)
        if not rs:
            continue
        c = Counter(r["suggested_failure"] for r in rs if r["suggested_failure"])
        body.append([LABELS.get(v, v), len(rs), sum(r["pass"] for r in rs), *[c[f] for f in score.FAIL_LABELS]])
    return table(["Variant", "runs", "passed", *score.FAIL_LABELS], body)


def percentile(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else float("nan")


def cost(rows, eval_name) -> tuple[str, list[str]]:
    body, warns = [], []
    for v in ordered(rows):
        rs = of(rows, v, eval_name)
        if not rs:
            continue
        lat = [r["latency_s"] or 0 for r in rs]
        hits = sum(int(r.get("a_cache_hits") or 0) for r in rs)
        calls = sum(int(r.get("a_llm_calls") or 0) for r in rs)
        if hits:
            warns.append(f"{v} on {eval_name}: {hits}/{calls} model calls were replayed from the cache; its latency is not real")
        body.append([LABELS.get(v, v), f"{statistics.median(lat):.1f}", f"{percentile(lat, 0.95):.1f}",
                     f"{score.mean(lat):.1f}", f"{score.mean([r['tokens_in'] for r in rs]):.0f}",
                     f"{score.mean([r['tokens_out'] for r in rs]):.0f}",
                     f"{score.mean([int(r.get('a_llm_calls') or 0) for r in rs]):.1f}" if calls else "n/a",
                     f"{score.mean([r['tool_calls'] for r in rs]):.2f}", f"{score.mean([r['steps'] or 0 for r in rs]):.1f}",
                     f"{hits}/{calls}" if calls else "n/a"])
    return table(["Variant", "median s", "p95 s", "mean s", "tokens in/q", "tokens out/q", "model calls/q",
                  "tool calls/q", "steps/q", "cache hits"], body), warns


def checks(rows, eval_name) -> str:
    body = []
    for v in ordered(rows):
        rs = [r for r in of(rows, v, eval_name) if "a_status" in r]
        if not rs:
            continue
        n = len(rs)
        issues = Counter(c for r in rs for c in str(r.get("a_issues") or "").split("+") if c)
        verdicts = Counter(r.get("a_critic_verdict") or "-" for r in rs)
        body.append([LABELS.get(v, v), n, pct(sum(bool(r["a_blocked"]) for r in rs) / n),
                     sum(bool(r.get("a_report_only_block")) for r in rs),
                     pct(sum(bool(r["a_escalated"]) for r in rs) / n),
                     pct(score.mean([int(r["a_revisions"]) for r in rs])),
                     ", ".join(f"{k}={c}" for k, c in verdicts.most_common()) or "-",
                     ", ".join(f"{k}={c}" for k, c in issues.most_common(5)) or "none"])
    return table(["Variant", "runs", "answers blocked", "reported only", "escalated", "revisions/q", "critic verdicts",
                  "top validator issues"], body)


def hrms_table(rows) -> str:
    body = []
    for v in ordered(rows):
        rs = [r for r in of(rows, v, "hrms_ext_v1") if "calls_ok" in r]
        if not rs:
            continue
        n = len(rs)
        body.append([LABELS.get(v, v), n, pct(sum(r["pass"] for r in rs) / n), pct(sum(r["calls_ok"] for r in rs) / n),
                     pct(sum(r["call_args_ok"] for r in rs) / n), sum(r["forbidden_tool"] for r in rs),
                     sum(r["wrong_state"] for r in rs)])
    return table(["Variant", "runs", "pass", "right tools", "right arguments", "forbidden tool calls", "wrong state"], body)


def comparisons(rows, eval_name) -> str:
    out = []
    for a, b in COMPARISONS:
        sub = [r for r in rows if r["eval"] == eval_name and r["variant"] in (a, b)]
        if of(sub, a) and of(sub, b):
            out.append("- " + score.compare(sub, a, b))
    return "\n".join(out) or "- no pair of variants has records yet"


def verdict(rows) -> str:
    """One honest sentence on 'does the agent beat plain RAG?' from the paired test on eval_v1."""
    a, b = of(rows, "v2_rag", "eval_v1"), of(rows, "v3_agent", "eval_v1")
    if not a or not b:
        return "Not decided: need v2_rag and v3_agent records on eval_v1."
    per = lambda rs: {q: score.mean([r["pass"] for r in rs if r["qid"] == q]) for q in {r["qid"] for r in rs}}  # noqa: E731
    A, B = per(a), per(b)
    common = sorted(set(A) & set(B))
    x = sum(A[q] >= .5 and B[q] < .5 for q in common)
    y = sum(B[q] >= .5 and A[q] < .5 for q in common)
    p = score.mcnemar_exact(x, y)
    pa, pb = score.mean([A[q] for q in common]), score.mean([B[q] for q in common])
    if p < 0.05 and pb > pa:
        word = "V3 (agent) passes more questions than V2 (RAG)"
    elif p < 0.05:
        word = "V2 (RAG) passes more questions than V3 (agent)"
    else:
        word = "no reliable difference between V2 (RAG) and V3 (agent)"
    return (f"{word}: strict pass {pct(pa)} (V2) vs {pct(pb)} (V3) on {len(common)} questions, McNemar exact p={p:.3f}. "
            f"Read with the cost table: V3 spends more model calls and time.")


def sheets(rows, reviews, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for (v, e) in sorted({(r["variant"], r["eval"]) for r in rows}):
        rs = [r for r in rows if r["variant"] == v and r["eval"] == e]
        keys = sorted({k for r in rs for k in r})
        with open(out / f"scores_{v}__{e}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rs)
    with open(out / "needs_human.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["qid", "variant", "repeat", "reasons", "answer_text"])
        w.writerows(reviews)
    with open(out / "taxonomy_sheet.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["eval", "qid", "variant", "type", "suggested_failure", "human_label", "note"])
        for r in rows:
            if r["variant"].startswith(("v2", "v3")) and r["repeat"] == 0 and not r["pass"]:
                w.writerow([r["eval"], r["qid"], r["variant"], r["type"], r["suggested_failure"], "", ""])


def build(rows, reviews, warns, records) -> str:
    n_real = sum(1 for r in records if r.get("tag") != "mock")
    parts = ["# H9 results", "",
             f"Records: {len(records)} ({n_real} not mock). Scorer: evaluation/score.py (rule-based, no model judge).", ""]
    if warns:
        parts += ["## Warnings", *[f"- {w}" for w in warns], ""]
    parts += ["## Does the agent beat plain RAG?", "", verdict(rows), ""]
    cost_warns = []
    for e in evalset.NAMES:
        if not any(r["eval"] == e for r in rows):
            continue
        parts += [f"## {e}", "", "### Headline", "", headline(rows, e), "### Pass by question type (passed/runs)", "",
                  by_type(rows, e), "### Failure labels (SUGGESTED, a human confirms in taxonomy_sheet.csv)", "",
                  taxonomy(rows, e)]
        c, w = cost(rows, e)
        cost_warns += w
        parts += ["### Latency and cost", "", c, "### Checks and agent behaviour", "", checks(rows, e)]
        if e == "hrms_ext_v1":
            parts += ["### HRMS tool use", "", hrms_table(rows)]
        parts += ["### Paired comparisons (ablations are deltas against V3)", "", comparisons(rows, e), ""]
    if cost_warns:
        parts += ["## Latency warnings", *[f"- {w}" for w in cost_warns], ""]
    parts += ["## Reading notes", "",
              "- Strict pass is the figure to report; lenient counts borderline fact matches and is an upper bound.",
              "- Failure labels and `needs_human.csv` rows are unconfirmed until a person has gone through the sheets.",
              "- The eval sets are DRAFT until the human review of the answers is done (UNVERIFIED if finished).",
              "- Ablations run with fewer repeats than the main variants; their intervals are wider.", ""]
    return "\n".join(parts)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", type=Path, default=OUT_DIR)
    p.add_argument("--out", type=Path)
    p.add_argument("--no-baselines", action="store_true", help="skip the H3 v0_* records")
    a = p.parse_args(argv)
    records = load_records(a.runs, not a.no_baselines)
    if not records:
        print(f"no run records under {a.runs}. Run: python -m evaluation.h9_run --stage main")
        return 1
    rows, reviews, warns = score_all(records)
    out = a.out or a.runs / "report"
    sheets(rows, reviews, out)
    text = build(rows, reviews, warns, records)
    (out / "report.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"\nwrote report.md, scores_*.csv, needs_human.csv, taxonomy_sheet.csv to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

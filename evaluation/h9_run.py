"""H9: run the whole comparison, one variant x eval set at a time.

    python -m evaluation.h9_run --dry-run                 # print the plan and the time estimate, run nothing
    python -m evaluation.h9_run --stage main              # v2_rag + v3_agent on eval_v1 and hrms_ext_v1, 3 repeats
    python -m evaluation.h9_run --stage ablate            # no critic, lenient citations, no blocking, critic revise
    python -m evaluation.h9_run --stage main --variants v3_agent --evals eval_v1   # a part of a stage

Records go to evaluation/results/h9/<variant>__<eval>.jsonl. Re-running the same command resumes
(finished records are skipped). Real endpoint only: the key and CHAT_* variables come from the
environment, never from a file (see README). Before the first call it checks that retrieval exists,
that both eval sets are frozen and that the endpoint is not a local stand-in (``--allow-mock`` for tests).

The disk cache is switched OFF (LLM_CACHE=0) so latency is real; ``--use-cache`` keeps it on
(fast, but latency and token numbers from replayed answers must not be reported).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from evaluation import evalset, run_eval
from evaluation.agent_variants import get_search
from evaluation.variants import VARIANTS

OUT_DIR = run_eval.RESULTS_DIR / "h9"
EVALS = ("eval_v1", "hrms_ext_v1")
SIZES = {"eval_v1": 80, "hrms_ext_v1": 20}
# rough seconds per question (H5 plan: about 2 s per model call; v2 makes one, v3 about 8). UNVERIFIED until run.
SECONDS = {"v2_rag": 3, "v3_agent": 16, "v3_agent_nocritic": 14, "v3_agent_lenient": 16,
           "v3_agent_noblock": 16, "v3_agent_revise": 20}
STAGES = {
    "main": {"variants": ("v2_rag", "v3_agent"), "evals": EVALS, "repeats": 3},
    "ablate": {"variants": ("v3_agent_nocritic", "v3_agent_lenient", "v3_agent_noblock", "v3_agent_revise"),
               "evals": ("eval_v1",), "repeats": 1},
}


def plan(stages: list[str], variants=None, evals=None, repeats=None, ablation_repeats=None) -> list[dict]:
    """Ordered jobs: [{variant, eval, repeats, questions, minutes}]."""
    jobs = []
    for s in stages:
        cfg = STAGES[s]
        reps = repeats or (ablation_repeats if s == "ablate" and ablation_repeats else cfg["repeats"])
        for v in cfg["variants"]:
            for e in cfg["evals"]:
                if (variants and v not in variants) or (evals and e not in evals):
                    continue
                n = SIZES[e] * reps
                jobs.append({"variant": v, "eval": e, "repeats": reps, "questions": n,
                             "minutes": round(n * SECONDS[v] / 60, 1), "stage": s})
    return jobs


def preflight(allow_mock: bool) -> list[str]:
    """Problems that would waste a long run. Empty list means go."""
    problems = []
    for e in EVALS:
        if not evalset.is_frozen(e):
            problems.append(f"{e} is not frozen (or changed since): scripts/freeze_eval.py")
    try:
        get_search()
    except SystemExit as exc:
        problems.append(str(exc))
    if run_eval.is_mock() and not allow_mock:
        problems.append("CHAT_BASE_URL is a local stand-in: numbers would be plumbing only (--allow-mock to test)")
    if not os.environ.get("API_KEY") and not run_eval.is_mock():
        problems.append("API_KEY is not set in the environment (read -s API_KEY; export API_KEY)")
    return problems


def run_jobs(jobs: list[dict], out_dir: Path = OUT_DIR, log=print, official: bool = True) -> list[dict]:
    done = []
    for j in jobs:
        out = out_dir / f"{j['variant']}__{j['eval']}.jsonl"
        log(f"\n=== {j['variant']} on {j['eval']} x{j['repeats']} (about {j['minutes']} min) -> {out}")
        t0 = time.perf_counter()
        counts = run_eval.run(j["variant"], j["eval"], j["repeats"], out, official=official, log=log)
        done.append({**j, **counts, "seconds": round(time.perf_counter() - t0, 1), "file": str(out)})
        log(f"finished: {counts}")
    return done


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stage", choices=[*STAGES, "all"], default="main")
    p.add_argument("--variants", help="comma-separated subset of the stage")
    p.add_argument("--evals", help="comma-separated subset of the stage")
    p.add_argument("--repeats", type=int, help="override the repeats for every job")
    p.add_argument("--ablation-repeats", type=int, help="repeats for the ablation stage (default 1)")
    p.add_argument("--out-dir", type=Path, default=OUT_DIR)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--use-cache", action="store_true", help="keep the disk cache on: fast, latency not real")
    p.add_argument("--allow-mock", action="store_true", help="tests only")
    p.add_argument("--no-official", action="store_true", help="do not require frozen eval hashes (tests only)")
    a = p.parse_args(argv)
    split = lambda s: tuple(s.split(",")) if s else None  # noqa: E731
    stages = list(STAGES) if a.stage == "all" else [a.stage]
    jobs = plan(stages, split(a.variants), split(a.evals), a.repeats, a.ablation_repeats)
    unknown = {j["variant"] for j in jobs} - set(VARIANTS)
    if unknown:
        raise SystemExit(f"unregistered variants: {sorted(unknown)}")
    total = sum(j["minutes"] for j in jobs)
    print("Plan:")
    for j in jobs:
        print(f"  {j['variant']:<20} {j['eval']:<12} x{j['repeats']}  {j['questions']:>4} runs  ~{j['minutes']} min")
    print(f"  total ~{total:.0f} min ({total / 60:.1f} h), sequential (one request at a time). UNVERIFIED estimate.")
    if a.dry_run:
        return 0
    if not a.use_cache:
        os.environ["LLM_CACHE"] = "0"
    problems = [] if a.no_official and a.allow_mock else preflight(a.allow_mock)
    if problems:
        print("\nNot starting:\n  - " + "\n  - ".join(problems))
        return 2
    try:
        run_jobs(jobs, a.out_dir, official=not a.no_official)
    except KeyboardInterrupt:
        print("\nInterrupted. Finished records are saved; re-run the same command to resume.")
        return 130
    print(f"\nAll jobs finished. Next: python -m evaluation.h9_report  (reads {a.out_dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

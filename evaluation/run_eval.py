"""Run one variant over an eval set and append one run record per question and repeat.

    python -m evaluation.run_eval --variant demo --eval eval_v1 --repeats 3
    python -m evaluation.run_eval --variant v2_rag --qids Q001,Q002 --out /tmp/try.jsonl

- Sequential: the shared key allows one request at a time.
- Resumable: records already in the output file (same qid, variant, repeat) are skipped, and
  every record is flushed as it is written, so Ctrl-C or a dropped connection loses nothing.
- A pipeline exception becomes a record with ``error`` set. Exhausted retries on 429/503
  stop the whole run instead (every remaining question would fail the same way).
- ``--official`` refuses to run unless the eval file still matches its frozen hash.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from evaluation import evalset
from evaluation.records import RunRecord
from evaluation.variants import VARIANTS

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def is_mock() -> bool:
    """True if the chat endpoint is a local stand-in (tools/mock_llm), so numbers are plumbing only."""
    from hr_navigator.llm.config import load_dotenv
    load_dotenv(Path(".env"))
    return urlparse(os.environ.get("CHAT_BASE_URL", "")).hostname in ("127.0.0.1", "localhost")


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def done_keys(out: Path) -> set[tuple[str, str, int]]:
    if not out.is_file():
        return set()
    keys = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            keys.add((r["qid"], r["variant"], r.get("repeat", 0)))
    return keys


def run(variant: str, eval_name: str, repeats: int, out: Path, qids=None, types=None, limit=None,
        official: bool = False, log=print) -> dict:
    """Returns {"written": n, "skipped": n, "errors": n}."""
    from hr_navigator.llm.http import LLMCapacityError

    if variant not in VARIANTS:
        raise SystemExit(f"unknown variant '{variant}'. Registered: {', '.join(sorted(VARIANTS))}")
    if official and not evalset.is_frozen(eval_name):
        raise SystemExit(f"--official: {eval_name} has no matching freeze file. Run scripts/freeze_eval.py "
                         "or drop --official.")

    items = evalset.load(eval_name, qids, types, limit)
    sha, commit, mock = evalset.sha256(eval_name), git_commit(), is_mock()
    if mock:
        log("WARNING: CHAT_BASE_URL is a local stand-in. Records are tagged 'mock'; do not report them.")
    done = done_keys(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    total = len(items) * repeats
    counts = {"written": 0, "skipped": 0, "errors": 0}
    started = time.perf_counter()

    with out.open("a", encoding="utf-8") as f:
        for repeat in range(repeats):  # repeat outermost: a stopped run still has complete repeats
            for item in items:
                if (item["qid"], variant, repeat) in done:
                    counts["skipped"] += 1
                    continue
                t0 = time.perf_counter()
                try:
                    record = VARIANTS[variant](item, repeat)
                except LLMCapacityError as exc:
                    raise SystemExit(f"Stopped at {item['qid']} repeat {repeat}: endpoint at capacity ({exc}). "
                                     "Re-run the same command later; finished records are kept.")
                except Exception as exc:  # noqa: BLE001 - one bad question must not end the run
                    record = RunRecord(qid=item["qid"], variant=variant, repeat=repeat,
                                       error=f"{type(exc).__name__}: {str(exc)[:300]}",
                                       latency_s=round(time.perf_counter() - t0, 2))
                    counts["errors"] += 1
                record.qid, record.variant, record.repeat = item["qid"], variant, repeat
                record.eval_name, record.eval_sha, record.git_commit = eval_name, sha, commit
                record.date = datetime.date.today().isoformat()
                record.tag = "mock" if mock else ""
                f.write(record.model_dump_json() + "\n")
                f.flush()
                counts["written"] += 1
                n = counts["written"] + counts["skipped"]
                eta = (time.perf_counter() - started) / max(counts["written"], 1) * (total - n)
                log(f"[{n}/{total}] {item['qid']} r{repeat} {time.perf_counter() - t0:.1f}s "
                    f"{'ERROR' if record.error else 'ok'}  eta {eta / 60:.1f} min")
    return counts


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--variant", required=True)
    p.add_argument("--eval", default="eval_v1", choices=evalset.NAMES)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--qids", help="comma-separated qids")
    p.add_argument("--types", help="comma-separated question types")
    p.add_argument("--limit", type=int)
    p.add_argument("--out", type=Path, help="default: evaluation/results/<variant>__<eval>.jsonl")
    p.add_argument("--official", action="store_true", help="require the frozen eval hash to match")
    a = p.parse_args(argv)
    out = a.out or RESULTS_DIR / f"{a.variant}__{a.eval}.jsonl"
    split = lambda s: s.split(",") if s else None  # noqa: E731
    try:
        counts = run(a.variant, a.eval, a.repeats, out, split(a.qids), split(a.types), a.limit, a.official)
    except KeyboardInterrupt:
        print("\nInterrupted. Finished records are saved; re-run the same command to resume.")
        return 130
    print(f"done: {counts['written']} written, {counts['skipped']} already done, {counts['errors']} errors -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

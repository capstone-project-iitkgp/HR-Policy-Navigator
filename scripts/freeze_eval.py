"""Freeze the eval sets: record SHA-256 hashes so results can prove which questions they used.

    python scripts/freeze_eval.py            # writes data/eval/<name>.sha256 and data/eval/FROZEN.md
    python scripts/freeze_eval.py --check    # exit 1 if a file no longer matches its hash

After a freeze the eval files change only as a new version (v1.1) with a changelog.
The script does not create the git tag; it prints the command.
"""

from __future__ import annotations

import datetime
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation import evalset  # noqa: E402

VALIDATOR = Path(__file__).resolve().parents[2] / "05_My_Task_Eval_Set" / "validate_eval.py"


def validate() -> None:
    """Run the author's validator on eval_v1 if it is next to the repo (it needs the manifest beside it)."""
    if not VALIDATOR.is_file():
        print("note: validate_eval.py not found next to the repo, skipping the quote check")
        return
    r = subprocess.run([sys.executable, "-I", str(VALIDATOR), str(evalset.path("eval_v1"))],
                       capture_output=True, text=True)
    print(r.stdout[-600:] + r.stderr[-300:])
    if r.returncode != 0:
        raise SystemExit("validate_eval.py failed: fix the eval set before freezing")


def check() -> int:
    bad = [n for n in evalset.NAMES if not evalset.is_frozen(n)]
    for n in evalset.NAMES:
        print(f"{n}: {'OK' if n not in bad else 'MISMATCH or not frozen'}")
    return 1 if bad else 0


def freeze() -> None:
    validate()
    lines = [f"# Eval sets frozen {datetime.date.today().isoformat()}", ""]
    for name in evalset.NAMES:
        items = evalset.load(name)
        sha = evalset.sha256(name)
        (evalset.EVAL_DIR / f"{name}.sha256").write_text(f"{sha}  {name}.jsonl\n")
        status = Counter(i.get("authorship", {}).get("status", "?") for i in items)
        reviewed = sum(1 for i in items if i.get("authorship", {}).get("reviewer"))
        lines += [f"- `{name}.jsonl`: {len(items)} items, SHA-256 `{sha}`",
                  f"  - authorship status: {dict(status)}; items with a named human reviewer: {reviewed}"]
        print(f"{name}: {len(items)} items  {sha}")
    lines += ["", "**Human review pending.** Frozen on 20 Nov 2026 so the baselines and later runs use fixed questions.",
              "Every result table must say the gold answers were AI-drafted and not yet human-verified, "
              "until the reviewers (Sakshi, Srinivas, Himadri) are recorded in each item's `authorship`.",
              "A later change is a new version (v1.1) with a changelog, not an edit to these files.", "",
              "Tag after committing: `git tag eval-v1`"]
    (evalset.EVAL_DIR / "FROZEN.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(check())
    freeze()

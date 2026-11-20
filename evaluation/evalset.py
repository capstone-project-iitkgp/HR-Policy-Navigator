"""Load the eval sets and check them against their frozen hash."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1] / "data" / "eval"
NAMES = ("eval_v1", "hrms_ext_v1")


def path(name: str) -> Path:
    if name not in NAMES:
        raise ValueError(f"unknown eval set '{name}', expected one of {NAMES}")
    return EVAL_DIR / f"{name}.jsonl"


def sha256(name: str) -> str:
    return hashlib.sha256(path(name).read_bytes()).hexdigest()


def frozen_sha(name: str) -> str | None:
    """The hash written by scripts/freeze_eval.py, or None if never frozen."""
    f = EVAL_DIR / f"{name}.sha256"
    return f.read_text().split()[0] if f.is_file() else None


def is_frozen(name: str) -> bool:
    """True only if a freeze file exists and the data still matches it."""
    return frozen_sha(name) == sha256(name)


def load(name: str, qids: list[str] | None = None, types: list[str] | None = None,
         limit: int | None = None) -> list[dict]:
    items = [json.loads(line) for line in path(name).read_text(encoding="utf-8").splitlines() if line.strip()]
    if qids:
        items = [q for q in items if q["qid"] in qids]
    if types:
        items = [q for q in items if q["type"] in types]
    return items[:limit] if limit else items

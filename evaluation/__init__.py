"""Evaluation harness: run variants over the frozen eval set, then score the run records."""

import sys
from pathlib import Path

# `python -m evaluation.run_eval` from Code/ should work without PYTHONPATH=src (pytest.ini does this for tests).
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

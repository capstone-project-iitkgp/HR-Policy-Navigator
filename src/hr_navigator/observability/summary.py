"""Read traces back and summarise them: per trace, latency by stage, tokens, calls, errors.

Used by ``scripts/ask.py --trace``, ``evaluation.dev_run --trace`` and the trace page (H7 step 4);
H9 can reuse ``summarize`` for the latency and token-cost table. Pure functions over span dicts
(the JSONL records written by ``tracing.JsonlSink``), no model calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from hr_navigator.observability import tracing

KIND, INPUT, OUTPUT = tracing.KIND, tracing.INPUT, tracing.OUTPUT
STAGES = ("plan", "executor", "answer", "validate_response", "critic", "escalate_to_hr")


def load(directory: Path | str | None = None, day: str | None = None) -> list[dict[str, Any]]:
    """All spans in ``<directory>/<day>.jsonl`` (every day if ``day`` is None). Bad lines are skipped."""
    d = Path(directory or tracing.DEFAULT_DIR)
    files = [d / f"{day}.jsonl"] if day else sorted(d.glob("*.jsonl"))
    spans = []
    for f in files:
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                spans.append(json.loads(line))
            except ValueError:
                continue
    return spans


def by_trace(spans: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """trace_id -> its spans, oldest trace first."""
    out: dict[str, list[dict[str, Any]]] = {}
    for s in spans:
        out.setdefault(s["trace_id"], []).append(s)
    return dict(sorted(out.items(), key=lambda kv: min(s["start_ns"] for s in kv[1])))


def _ms(s: dict[str, Any]) -> float:
    return (s["end_ns"] - s["start_ns"]) / 1e6


def _kind(s: dict[str, Any]) -> Any:
    return s["attributes"].get(KIND)


def summarize(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """One trace -> a flat dict. A trace with no root span (cut off) still summarises, with root=False."""
    root = next((s for s in spans if s["parent_id"] is None), None)
    a = root["attributes"] if root else {}
    llm = [s for s in spans if _kind(s) == "LLM"]
    tools = [s for s in spans if _kind(s) == "TOOL" and not s["attributes"].get("app.by_code")]
    # Stage times are exclusive: validate_response and critic run inside the answer span, so their
    # time is taken out of "answer" (which then means the answer and revision model calls).
    stage_ms: dict[str, float] = {}
    for s in spans:
        if s["name"] in STAGES:
            stage_ms[s["name"]] = stage_ms.get(s["name"], 0.0) + _ms(s)
    if "answer" in stage_ms:
        stage_ms["answer"] = max(0.0, stage_ms["answer"] - stage_ms.get("validate_response", 0.0) - stage_ms.get("critic", 0.0))
    return {
        "trace_id": spans[0]["trace_id"] if spans else "",
        "start_ns": min((s["start_ns"] for s in spans), default=0),
        "question": a.get(INPUT, ""), "answer": a.get(OUTPUT, ""),
        "behaviour": a.get("app.behaviour", ""), "status": a.get("app.status", ""),
        "employee": a.get(tracing.USER, ""),
        "latency_ms": _ms(root) if root else 0.0,
        "stage_ms": stage_ms,
        "llm_ms": sum(_ms(s) for s in llm), "tool_ms": sum(_ms(s) for s in tools),
        "tokens_in": sum(s["attributes"].get(tracing.TOK_IN, 0) for s in llm),
        "tokens_out": sum(s["attributes"].get(tracing.TOK_OUT, 0) for s in llm),
        "llm_calls": len(llm), "cache_hits": sum(1 for s in llm if s["attributes"].get("app.cached")),
        "tool_calls": len(tools), "blocked_tool_calls": sum(1 for s in tools if s["attributes"].get("app.blocked") is True),
        "errors": sum(1 for s in spans if s["status"] == "error"),
        "root": root is not None,
    }


def format_summary(s: dict[str, Any]) -> str:
    """Plain text for the terminal."""
    lines = [f"trace {s['trace_id']}  {s['latency_ms'] / 1000:.1f} s  {s['status'] or '-'}  behaviour {s['behaviour'] or '-'}",
             f"  model calls {s['llm_calls']} ({s['cache_hits']} cached, {s['llm_ms'] / 1000:.1f} s)   "
             f"tool calls {s['tool_calls']} ({s['blocked_tool_calls']} blocked, {s['tool_ms'] / 1000:.2f} s)   "
             f"tokens {s['tokens_in']} in + {s['tokens_out']} out   error spans {s['errors']}"]
    for name in STAGES:
        if name in s["stage_ms"]:
            lines.append(f"  {name:<18}{s['stage_ms'][name] / 1000:>7.2f} s")
    return "\n".join(lines)


def span_rows(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The spans of one trace in reading order (parent before children, siblings by start time), each with
    its depth, start offset and duration in ms. A span whose parent is missing is shown at the top level."""
    ids = {s["span_id"] for s in spans}
    kids: dict[str | None, list[dict[str, Any]]] = {}
    for s in sorted(spans, key=lambda s: (s["start_ns"], s["end_ns"])):
        kids.setdefault(s["parent_id"] if s["parent_id"] in ids else None, []).append(s)
    t0 = min((s["start_ns"] for s in spans), default=0)
    rows: list[dict[str, Any]] = []

    def walk(parent: str | None, depth: int) -> None:
        for s in kids.get(parent, []):
            rows.append({"depth": depth, "name": s["name"], "kind": _kind(s) or "", "status": s["status"],
                         "start_ms": (s["start_ns"] - t0) / 1e6, "duration_ms": _ms(s),
                         "attributes": s["attributes"], "span_id": s["span_id"]})
            walk(s["span_id"], depth + 1)

    walk(None, 0)
    return rows

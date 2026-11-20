"""Trace viewer: lists the agent's traces (written with TRACE=1 or --trace) and shows one as a span tree.

    cd Code
    .venv/bin/streamlit run app/trace_page.py

Reads ``.cache/traces/<date>.jsonl`` (or the folder in TRACE_DIR). Read-only; it never calls the model.
"""

import datetime
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

import streamlit as st  # noqa: E402

from hr_navigator.observability import summary, tracing  # noqa: E402

st.set_page_config(page_title="Agent traces", layout="wide")
st.title("Agent traces")

directory = Path(os.environ.get("TRACE_DIR") or tracing.DEFAULT_DIR)
days = sorted((p.stem for p in directory.glob("*.jsonl")), reverse=True) if directory.exists() else []

with st.sidebar:
    st.caption(f"Folder: {directory}")
    day = st.selectbox("Day", ["all"] + days, key="day")
    errors_only = st.checkbox("Only traces with errors", key="errors_only")
    st.button("Reload", key="reload")

traces = summary.by_trace(summary.load(directory, None if day == "all" else day))
sums = [summary.summarize(t) for t in traces.values()]
if errors_only:
    sums = [s for s in sums if s["errors"]]
sums.sort(key=lambda s: s["start_ns"], reverse=True)

if not sums:
    st.info("No traces yet. Run `python scripts/ask.py \"...\" --trace` or set TRACE=1, then reload.")
    st.stop()


def when(s):
    return datetime.datetime.fromtimestamp(s["start_ns"] / 1e9).strftime("%d %b %H:%M:%S")


st.dataframe([{"Time": when(s), "Question": s["question"][:80], "Behaviour": s["behaviour"], "Status": s["status"],
               "Seconds": round(s["latency_ms"] / 1000, 1), "Tokens": s["tokens_in"] + s["tokens_out"],
               "Model calls": s["llm_calls"], "Tool calls": s["tool_calls"], "Errors": s["errors"]} for s in sums],
             hide_index=True, width="stretch")

labels = {f"{when(s)}  {s['question'][:60]}  [{s['trace_id'][:8]}]": s for s in sums}
chosen = labels[st.selectbox("Trace", list(labels), key="trace")]

st.subheader("Summary")
c = st.columns(5)
c[0].metric("Seconds", f"{chosen['latency_ms'] / 1000:.1f}")
c[1].metric("Tokens in / out", f"{chosen['tokens_in']} / {chosen['tokens_out']}")
c[2].metric("Model calls", f"{chosen['llm_calls']} ({chosen['cache_hits']} cached)")
c[3].metric("Tool calls", f"{chosen['tool_calls']} ({chosen['blocked_tool_calls']} blocked)")
c[4].metric("Error spans", chosen["errors"])
st.write(f"**Question:** {chosen['question']}")
st.write(f"**Answer ({chosen['status'] or 'no result'}, {chosen['behaviour'] or '-'}):** {chosen['answer']}")
if chosen["stage_ms"]:
    st.dataframe([{"Stage": k, "Seconds": round(v / 1000, 2)} for k, v in chosen["stage_ms"].items()],
                 hide_index=True, key="stages")

st.subheader("Spans")
total = max(chosen["latency_ms"], 1.0)
for i, row in enumerate(summary.span_rows(traces[chosen["trace_id"]])):
    bar = "#" * max(1, round(20 * row["duration_ms"] / total))
    mark = "ERROR " if row["status"] == "error" else ""
    with st.expander(f"{'    ' * row['depth']}{mark}{row['name']}  ({row['kind']})  {row['duration_ms']:.0f} ms  {bar}",
                     expanded=False):
        st.caption(f"starts at +{row['start_ms']:.0f} ms")
        st.json(row["attributes"])

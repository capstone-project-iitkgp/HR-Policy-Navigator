"""Variants: the one seam between the runner and the things we compare.

A variant is a function ``run(item, repeat) -> RunRecord`` registered under a name.
The runner fills qid, variant, repeat and the run stamps; the variant fills the answer,
citations, retrieved chunks, tool calls, token counts and latency.

H3 adds the prompt baselines (v0_*), H9 adds v2_rag and v3_agent.
"""

from __future__ import annotations

from typing import Callable

from evaluation.records import Citation, RetrievedChunk, RunRecord, ToolCall

Variant = Callable[[dict, int], RunRecord]
VARIANTS: dict[str, Variant] = {}


def register(name: str):
    def wrap(fn: Variant) -> Variant:
        VARIANTS[name] = fn
        return fn
    return wrap


DEMO_PREFIX = {
    "refuse_not_in_corpus": "The documents do not say.", "clarify": "Could you tell me which one?",
    "flag_conflict": "The two policies conflict.", "refuse_injection": "I cannot follow that instruction.",
    "refuse_pii": "I cannot share that, it is confidential.", "escalate_to_human": "Please contact HR.",
    "draft_for_confirmation": "I created a draft; please confirm.",
}


@register("demo")
def demo(item: dict, repeat: int) -> RunRecord:
    """Plays back the gold answer, citations and tools. For testing the harness, not a result."""
    sources = item.get("gold_sources") or [{}]
    quotes = item.get("evidence_quotes", [])
    return RunRecord(
        qid=item["qid"], variant="demo", repeat=repeat,
        answer_text=(DEMO_PREFIX.get(item["expected_behaviour"], "") + " " + item["gold_answer"]).strip(),
        citations=[Citation(doc_id=sources[min(i, len(sources) - 1)].get("doc_id", ""), quote=q)
                   for i, q in enumerate(quotes)],
        retrieved=[RetrievedChunk(chunk_id=f"c{i}", doc_id=sources[min(i, len(sources) - 1)].get("doc_id", ""), text=q)
                   for i, q in enumerate(quotes)],
        tool_calls=[ToolCall(name=t, args={}) for t in item.get("expected_tools", [])],
        steps=len(item.get("expected_tools", [])) + 1, model="demo",
    )

"""Keep prompts inside the 8192-token window (prompt + output).

There is no Qwen tokenizer in the project, so tokens are estimated as characters / 3.
That over-counts English (about 4 chars per token) on purpose: a prompt that is a little
too short is harmless, one that is too long is a 400 error.
"""

from __future__ import annotations

import json
from typing import Any

from hr_navigator.schemas import Passage

CONTEXT_LIMIT = 8192
OUTPUT_RESERVE = 700  # room left for the reply
CHARS_PER_TOKEN = 3
TOOL_SHARE = 0.4  # tool results may use at most this share of the free space
MIN_PASSAGE_CHARS = 200  # a passage cut shorter than this is dropped instead


def estimate_tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)  # ceiling


def fit_context(
    fixed_text: str,
    passages: list[Passage],
    tool_results: list[Any],
    *,
    limit: int = CONTEXT_LIMIT,
    reserve: int = OUTPUT_RESERVE,
) -> tuple[list[Passage], list[str], list[str]]:
    """Choose what fits next to ``fixed_text`` (prompt template, question, profile, plan).

    Returns (passages, tool_result_strings, notes). Tool results are cut first to their
    share, then passages (best first) fill the rest: whole passages while they fit, the
    last one shortened, the remainder dropped. ``notes`` says what was trimmed, for the trace.
    """
    notes: list[str] = []
    free = (limit - reserve - estimate_tokens(fixed_text)) * CHARS_PER_TOKEN  # characters
    free = max(free, 0)

    texts = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in tool_results]
    tool_cap = int(free * TOOL_SHARE)
    if texts and sum(map(len, texts)) > tool_cap:
        per = max(tool_cap // len(texts), 0)
        texts = [t if len(t) <= per else t[:per] + "...(cut)" for t in texts]
        notes.append(f"tool results cut to {per} chars each")
    free -= sum(map(len, texts))

    kept: list[Passage] = []
    for i, p in enumerate(passages):
        cost = len(p.text) + len(p.title) + len(p.chunk_id) + len(p.doc_id) + 12  # header overhead
        if cost <= free:
            kept.append(p)
            free -= cost
            continue
        room = free - (cost - len(p.text))
        if room >= MIN_PASSAGE_CHARS:
            kept.append(p.model_copy(update={"text": p.text[:room]}))
            free = 0
        notes.append(f"passages trimmed: kept {len(kept)} of {len(passages)}")
        break
    return kept, texts, notes

"""Turn passages into prompt text, and check an answer's citations against them in code.

The model is told to copy quotes exactly; this is where that is verified, because a prompt
cannot guarantee it (the H3 baselines invented quotes when asked to cite)."""

from __future__ import annotations

import re

from hr_navigator.prompts.schemas import FinalAnswer
from hr_navigator.schemas import Passage

MAX_PASSAGE_CHARS = 1400  # about 300 tokens; five passages fit well inside the 8192 limit


def _norm(s: str) -> str:
    s = (s or "").replace("­", "").replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def format_passages(passages: list[Passage]) -> str:
    """``[chunk_id] title (doc_id)`` then the text. Empty list gives an explicit marker."""
    if not passages:
        return "(no passages were found)"
    blocks = []
    for p in passages:
        head = f"[{p.chunk_id}] {p.title} ({p.doc_id})".replace("  ", " ").strip()
        blocks.append(f"{head}\n{p.text[:MAX_PASSAGE_CHARS]}")
    return "\n\n".join(blocks)


def check_citations(answer: FinalAnswer, passages: list[Passage]) -> list[str]:
    """Problems with the citations; empty means every quote is real. Used by the agent and by H6."""
    by_id = {p.chunk_id: _norm(p.text) for p in passages}
    problems = []
    for c in answer.citations:
        if c.passage_id not in by_id:
            problems.append(f"passage '{c.passage_id}' was not retrieved")
        elif _norm(c.quote) not in by_id[c.passage_id]:
            problems.append(f"quote is not in passage '{c.passage_id}': {c.quote[:60]!r}")
    if answer.behaviour == "answer" and not answer.citations:
        problems.append("an answer from policy needs at least one citation")
    return problems

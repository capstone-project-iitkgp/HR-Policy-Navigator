"""Turn passages into prompt text, and check an answer's citations against them in code.

The model is told to copy quotes exactly; this is where that is verified, because a prompt
cannot guarantee it (the H3 baselines invented quotes when asked to cite)."""

from __future__ import annotations

import difflib
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
            problems.append(f"quote is not in passage '{c.passage_id}': {c.quote[:200]!r}")
    if answer.behaviour == "answer" and not answer.citations:
        problems.append("an answer from policy needs at least one citation")
    return problems


def _canon(num: str) -> str:
    """6, 6.0 and 06 are the same number; so are 1,000 and 1000."""
    try:
        return f"{float(num.replace(',', '')):g}"
    except ValueError:
        return num


def unsupported_numbers(answer_text: str, *sources: str) -> list[str]:
    """Digit strings in the answer that appear in none of the sources (passages, tool results, the question).

    A cheap code check for invented figures; it does not see number words ("seven"), so it is
    a floor, not a proof."""
    pattern = r"\d+(?:[.,]\d+)?"
    seen = {_canon(n) for n in re.findall(pattern, " ".join(_norm(s) for s in sources))}
    return sorted({n for n in re.findall(pattern, answer_text) if _canon(n) not in seen})


SNAP_MIN_RATIO = 0.85


def snap_quotes(answer: FinalAnswer, passages: list[Passage]) -> tuple[FinalAnswer, int]:
    """Replace a nearly-verbatim quote (a dropped or changed word) by the passage's own sentence.

    The model often copies a long sentence with one word off. If a sentence (or two adjacent
    sentences) of the cited passage matches at least 85%, the quote becomes that exact text, so
    it verifies. Quotes that match nothing are left alone and fail the check as before.
    Returns the answer and the number of quotes repaired."""
    by_id = {p.chunk_id: p.text for p in passages}
    fixed, citations = 0, []
    for c in answer.citations:
        text = by_id.get(c.passage_id)
        if text is None or _norm(c.quote) in _norm(text):
            citations.append(c)
            continue
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        windows = sentences + [f"{a} {b}" for a, b in zip(sentences, sentences[1:])]
        target = _norm(c.quote)
        best = max(windows, key=lambda w: difflib.SequenceMatcher(None, target, _norm(w)).ratio(), default="")
        if best and difflib.SequenceMatcher(None, target, _norm(best)).ratio() >= SNAP_MIN_RATIO:
            citations.append(c.model_copy(update={"quote": best}))
            fixed += 1
        else:
            citations.append(c)
    return answer.model_copy(update={"citations": citations}), fixed

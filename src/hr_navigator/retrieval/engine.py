"""search(query, k=5, filters=None) -> list[Passage]: the function the agent calls.

    from hr_navigator.retrieval import search
    search("How many casual leave days do I get?", k=5, filters={"org": "DEF"})

Modes (argument ``mode``, else the RETRIEVAL_MODE environment variable, else "auto"):
- "bm25": keyword ranking only. Works offline; the default when no embeddings are cached.
- "dense": embedding ranking only.
- "hybrid": both, merged with Reciprocal Rank Fusion.
- "auto": "hybrid" when every chunk has a cached embedding, else "bm25".
If the query embedding fails at run time, the call falls back to BM25 and records it in
``last_info["degraded"]``; it does not raise.

Filters (all optional, case-insensitive): ``doc_id``, ``org`` (full name or alias, ";" separates
several), ``jurisdiction`` and ``category`` (substring), ``synthetic`` (bool). An unknown key
raises ValueError.

Results: best first, at most ``k``, never a passage that looks like an injected instruction
(see safeguards/untrusted.py). ``Passage.scores`` holds the scores that were computed: "bm25",
"dense", "rrf". There is no reranker yet (see docs/decisions/a4_retrieval.md).

The index is built on the first call (well under a second) and kept for the process.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any

from hr_navigator.retrieval.bm25 import tokenize
from hr_navigator.retrieval.fuse import ordered, rrf
from hr_navigator.retrieval.index import Index, build_index

log = logging.getLogger(__name__)

MODES = ("auto", "bm25", "dense", "hybrid")
FILTER_KEYS = ("doc_id", "org", "jurisdiction", "category", "synthetic")
CANDIDATES = 50  # how deep each ranker looks before fusion

_index: Index | None = None
_lock = threading.Lock()
last_info: dict[str, Any] = {}


def get_index() -> Index:
    global _index
    with _lock:
        if _index is None:
            _index = build_index()
        return _index


def set_index(index: Index | None) -> None:
    """Use a given index (tests, the recall@k harness); None rebuilds from the manifest next call."""
    global _index
    with _lock:
        _index = index


def _word_match(a: str, b: str) -> bool:
    """True if one name contains the other as whole words ("Kentucky" in "University of Kentucky")."""
    a, b = a.strip().lower(), b.strip().lower()
    if not a or not b:
        return False
    short, long_ = sorted((a, b), key=len)
    return re.search(rf"(?<!\w){re.escape(short)}(?!\w)", long_) is not None


def _allowed(index: Index, filters: dict[str, Any] | None) -> list[int]:
    if not filters:
        return list(range(len(index.passages)))
    unknown = set(filters) - set(FILTER_KEYS)
    if unknown:
        raise ValueError(f"unknown filter key(s): {', '.join(sorted(unknown))}; allowed: {', '.join(FILTER_KEYS)}")
    keep = []
    for i, p in enumerate(index.passages):
        doc = index.docs[p.doc_id]
        ok = True
        for key, value in filters.items():
            if value is None or value == "":
                continue
            if key == "doc_id":
                ok = p.doc_id.lower() == str(value).lower()
            elif key == "org":
                wanted = [v for v in str(value).split(";") if v.strip()]
                ok = any(_word_match(w, n) for w in wanted for n in doc.org_names())
            elif key == "synthetic":
                ok = p.synthetic == bool(value)
            else:
                ok = str(value).lower() in getattr(p, key).lower()
            if not ok:
                break
        if ok:
            keep.append(i)
    return keep


def _resolve_mode(index: Index, mode: str | None) -> str:
    mode = (mode or os.environ.get("RETRIEVAL_MODE") or "auto").lower()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if mode == "auto":
        return "hybrid" if index.dense is not None else "bm25"
    if mode in ("dense", "hybrid") and index.dense is None:
        raise ValueError(f"mode {mode!r} needs embeddings; run `python -m hr_navigator.retrieval.build --dense`")
    return mode


def _top(scores, allowed: list[int], n: int, positive_only: bool) -> list[int]:
    pairs = [(scores[i], i) for i in allowed if not positive_only or scores[i] > 0]
    pairs.sort(key=lambda x: (-x[0], x[1]))
    return [i for _, i in pairs[:n]]


def search(query: str, k: int = 5, filters: dict | None = None, *, mode: str | None = None,
           index: Index | None = None) -> list:
    index = index or get_index()
    info: dict[str, Any] = {"query": query, "k": k, "filters": filters or {}, "degraded": False, "dropped": []}
    use = _resolve_mode(index, mode)
    matching = _allowed(index, filters)
    allowed = [i for i in matching if not index.flags[i]]
    info["dropped"] = [index.passages[i].chunk_id for i in matching if index.flags[i]]
    if not query or not query.strip() or k <= 0 or not allowed:
        info["mode"] = use
        last_info.clear(); last_info.update(info)
        return []

    bm25_scores = index.bm25.scores(tokenize(query, use_stem=index.use_stem))
    dense_scores = None
    if use in ("dense", "hybrid"):
        try:
            dense_scores = index.dense.scores(index.embedder.embed_query(query))
        except Exception as exc:  # endpoint down, key missing, capacity: keep answering with BM25
            log.warning("dense search failed, falling back to BM25: %s", exc)
            info["degraded"] = True
            info["error"] = type(exc).__name__
            use = "bm25"

    if use == "bm25":
        order = _top(bm25_scores, allowed, k, positive_only=True)
        fused = {}
    elif use == "dense":
        order = _top(dense_scores, allowed, k, positive_only=False)
        fused = {}
    else:
        lists = [_top(bm25_scores, allowed, CANDIDATES, True), _top(dense_scores, allowed, CANDIDATES, False)]
        fused = rrf([[str(i) for i in lst] for lst in lists])
        order = [int(i) for i in ordered(fused)[:k]]

    results = []
    for i in order:
        scores = {"bm25": round(float(bm25_scores[i]), 4)}
        if dense_scores is not None:
            scores["dense"] = round(float(dense_scores[i]), 4)
        if fused:
            scores["rrf"] = round(fused[str(i)], 5)
        results.append(index.passages[i].model_copy(update={"scores": scores}))
    info["mode"] = use
    info["returned"] = [p.chunk_id for p in results]
    last_info.clear(); last_info.update(info)
    return results

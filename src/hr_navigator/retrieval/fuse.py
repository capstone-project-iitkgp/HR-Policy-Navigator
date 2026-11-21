"""Reciprocal Rank Fusion: merge several ranked lists into one.

    rrf([["a", "b"], ["b", "c"]])  ->  {"b": 1/61 + 1/62, "a": 1/61, "c": 1/62}

Each list adds 1 / (k + rank) for every id it contains (rank starts at 1). k = 60 is the value
from the original paper (Cormack et al., 2009) and is not tuned here.
"""

from __future__ import annotations

K = 60


def rrf(rankings: list[list[str]], k: int = K) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def ordered(scores: dict[str, float]) -> list[str]:
    """Ids by score, highest first; ties keep first-seen order (Python sort is stable)."""
    return sorted(scores, key=lambda i: -scores[i])

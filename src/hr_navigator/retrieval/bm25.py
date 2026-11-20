"""Okapi BM25 keyword ranking, written out in full (about 50 lines, no dependency).

    index = BM25([tokenize(t) for t in texts])
    index.scores(tokenize("casual leave days"))   # one float per text, higher is better

score(d, q) = sum over query terms t of
    idf(t) * f(t,d) * (k1 + 1) / (f(t,d) + k1 * (1 - b + b * len(d) / avg_len))
idf(t) = ln(1 + (N - n(t) + 0.5) / (n(t) + 0.5))      (the "plus one" form, never negative)
"""

from __future__ import annotations

import math
import re
from collections import Counter

_WORD = re.compile(r"[a-z0-9]+")
# Very common words carry no signal in HR policy text; short list, kept readable.
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i if in is it its my of on or our "
    "so that the their there this to was we what when where which who will with you your am me "
    "any all get got"
    .split()
)


def stem(word: str) -> str:
    """Tiny suffix rule so 'employees'/'employee' and 'policies'/'policy' match. Not Porter.

    Off by default: on eval_v1 it lowered evidence recall@5 slightly (2 of 66 items).
    """
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokenize(text: str, *, use_stem: bool = False) -> list[str]:
    words = [w for w in _WORD.findall(text.lower()) if w not in STOPWORDS]
    return [stem(w) for w in words] if use_stem else words


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.lengths = [len(d) for d in docs]
        self.avg_len = (sum(self.lengths) / len(docs)) if docs else 0.0
        df = Counter(t for d in self.tf for t in d)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = []
        terms = [t for t in dict.fromkeys(query) if t in self.idf]  # unique, known terms only
        for tf, length in zip(self.tf, self.lengths):
            norm = self.k1 * (1 - self.b + self.b * length / self.avg_len) if self.avg_len else self.k1
            s = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + norm)
            out.append(s)
        return out

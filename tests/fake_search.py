"""A stand-in for Akash's search(): keyword overlap over a small list. Tests only."""

from hr_navigator.schemas import Passage


def make_search(passages: list[Passage]):
    calls: list[str] = []

    def search(query: str, k: int = 5, filters=None) -> list[Passage]:
        calls.append(query)
        words = {w for w in query.lower().split() if len(w) > 2}
        scored = [(sum(w in p.text.lower() for w in words), p) for p in passages]
        return [p for s, p in sorted(scored, key=lambda x: -x[0]) if s > 0][:k]

    search.calls = calls  # type: ignore[attr-defined]
    return search


CASUAL = Passage(doc_id="zeta-leave", chunk_id="z-1", title="Zeta Leave Policy",
                 text="Full-time employees get 12 casual leave days per year. " * 10)
CLAIMS = Passage(doc_id="zeta-claims", chunk_id="z-2", title="Zeta Claims",
                 text="Travel claims must be filed within 7 days of the trip.")

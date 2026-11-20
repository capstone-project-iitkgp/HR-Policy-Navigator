"""Policy retrieval (Akash). The agent imports ``search`` from here by name.

    from hr_navigator.retrieval import search
    search(query: str, k: int = 5, filters: dict | None = None) -> list[Passage]

Importing this package is cheap and needs no key: the index is built on the first search call.
See engine.py for modes, filters and fallbacks.
"""

from hr_navigator.retrieval.engine import get_index, search, set_index

__all__ = ["search", "get_index", "set_index"]

"""Shared test settings.

Search runs in keyword (BM25) mode in every test unless the test sets RETRIEVAL_MODE itself.
Once the embedding cache is full, "auto" mode becomes hybrid, and hybrid embeds each new query on
the real embeddings server: the tests would then call the network and depend on its answers.
tests/test_embed_live.py is the one test that calls the server on purpose.
"""

import pytest


@pytest.fixture(autouse=True)
def keyword_search_in_tests(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_MODE", "bm25")

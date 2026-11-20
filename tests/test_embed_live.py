"""Real embeddings endpoint check. Skipped unless EMBED_BASE_URL is set (in the shell or .env).

    .venv/bin/python -m pytest tests/test_embed_live.py -q      # Zscaler off, .env filled in

Makes one request with 2 texts and writes nothing to the shared cache.
"""

import os
from pathlib import Path

import numpy as np
import pytest

from hr_navigator.llm.config import load_dotenv
from hr_navigator.retrieval.dense import Embedder

load_dotenv(Path(".env"))
pytestmark = pytest.mark.skipif(not os.environ.get("EMBED_BASE_URL"), reason="EMBED_BASE_URL not set (live test)")


def test_real_endpoint_returns_unit_vectors_of_384(tmp_path):
    m = Embedder(cache_dir=tmp_path).embed(["casual leave days", "travel claim deadline"])
    assert m.shape == (2, 384)
    assert np.allclose(np.linalg.norm(m, axis=1), 1, atol=1e-3)
    assert float(m[0] @ m[1]) < 0.99  # two different texts, two different vectors

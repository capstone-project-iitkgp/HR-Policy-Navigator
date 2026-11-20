"""Dense (embedding) search: cosine similarity over a NumPy matrix.

    embedder = Embedder()                      # reads EMBED_* from the environment / .env
    vectors = embedder.embed(["casual leave"])  # shape (1, 384), rows have unit length
    index = DenseIndex(vectors_for_chunks)
    index.scores(query_vector)                 # one cosine score per chunk

Embeddings come from the course endpoint (bge-small-en-v1.5, 384 dimensions, at most 16 texts
per request, 512 tokens per text). Every vector is cached on disk under ``.cache/embeddings/``,
keyed by the model name and the exact text, so each chunk is embedded once.

``Embedder.cached_only`` never calls the endpoint. Search uses it to decide whether the dense
index is complete (``python -m hr_navigator.retrieval.build --dense`` fills the cache).

Not verified on the real endpoint from this laptop (no key or CA file here): the response shape is
the OpenAI one (``data[i].embedding``), as in the course quickstart. The query prefix below is the
one bge v1.5 recommends for short queries; its effect here is not measured.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from hr_navigator.llm import http
from hr_navigator.llm.config import Endpoint, embed_endpoint

CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "embeddings"
BATCH = 16  # endpoint limit
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
EMBED_LOCK = threading.Lock()  # one embeddings request at a time from this process


class EmbeddingUnavailable(RuntimeError):
    """No endpoint configured, or a vector is missing from the cache in cached-only mode."""


def _normalise(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


class Embedder:
    def __init__(self, endpoint: Endpoint | None = None, *, cache_dir: Path = CACHE_DIR,
                 post: Callable[[list[str]], list[list[float]]] | None = None, model: str | None = None):
        """``post`` replaces the HTTP call in tests; ``endpoint`` defaults to the environment."""
        self._endpoint = endpoint
        self._post = post
        self.model = model or (endpoint.model if endpoint else None)
        self.cache_dir = cache_dir

    # ---- configuration ----------------------------------------------------
    def _resolved(self) -> Endpoint:
        if self._endpoint is None:
            try:
                self._endpoint = embed_endpoint()
            except RuntimeError as exc:  # a variable is missing
                raise EmbeddingUnavailable(str(exc)) from exc
            self.model = self.model or self._endpoint.model
        return self._endpoint

    def _model(self) -> str:
        if self.model is None:
            self._resolved()
        return self.model or "unknown"

    # ---- cache --------------------------------------------------------------
    def _path(self, text: str) -> Path:
        key = hashlib.sha256(f"{self._model()}\n{text}".encode()).hexdigest()
        return self.cache_dir / self._model().replace("/", "_") / key[:2] / f"{key}.npy"

    def _load(self, text: str) -> np.ndarray | None:
        p = self._path(text)
        return np.load(p) if p.is_file() else None

    def _save(self, text: str, vector: np.ndarray) -> None:
        p = self._path(text)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp.npy")
        np.save(tmp, vector.astype(np.float32))
        tmp.replace(p)

    # ---- calls --------------------------------------------------------------
    def _call(self, texts: list[str]) -> list[list[float]]:
        if self._post is not None:
            return self._post(texts)
        ep = self._resolved()
        with http.make_client(ep) as client:
            reply = http.post_json(client, "/embeddings", {"model": ep.model, "input": texts}, lock=EMBED_LOCK)
        data = sorted(reply.get("data", []), key=lambda d: d.get("index", 0))
        if len(data) != len(texts):
            raise http.LLMHTTPError(200, f"expected {len(texts)} embeddings, got {len(data)}")
        return [d["embedding"] for d in data]

    def embed(self, texts: list[str], *, cached_only: bool = False) -> np.ndarray:
        """Unit-length vectors, one row per text, in input order."""
        rows: list[np.ndarray | None] = [self._load(t) for t in texts]
        missing = [i for i, r in enumerate(rows) if r is None]
        if missing and cached_only:
            raise EmbeddingUnavailable(f"{len(missing)} of {len(texts)} texts have no cached embedding")
        for start in range(0, len(missing), BATCH):
            batch = missing[start:start + BATCH]
            vectors = self._call([texts[i] for i in batch])
            for i, v in zip(batch, vectors):
                vec = _normalise(np.asarray([v], dtype=np.float32))[0]
                self._save(texts[i], vec)
                rows[i] = vec
        if not rows:
            return np.zeros((0, 0), dtype=np.float32)
        return _normalise(np.vstack(rows))

    def embed_query(self, query: str) -> np.ndarray:
        return self.embed([QUERY_PREFIX + query])[0]


class DenseIndex:
    def __init__(self, vectors: np.ndarray):
        self.vectors = _normalise(vectors) if len(vectors) else vectors

    def scores(self, query_vector: np.ndarray) -> np.ndarray:
        return self.vectors @ query_vector.astype(np.float32)

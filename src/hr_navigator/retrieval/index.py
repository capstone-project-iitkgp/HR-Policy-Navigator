"""The search index: chunks, a BM25 ranker and (when available) a dense matrix.

    index = build_index()                 # corpus_v1.csv, default chunk settings, dense if cached
    index.passages[i], index.bm25, index.dense

Each chunk is indexed with a short header (document title, organisation, aliases, jurisdiction)
in front of its text. The header is used only for ranking, so a question that names "DEF" or
"Kentucky" finds that organisation's policy. ``Passage.text`` stays the exact chunk text.

Building takes well under a second for the current corpus, so the index is rebuilt from the
manifest at start-up and not stored. Only embeddings are cached on disk (see dense.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from hr_navigator.ingestion.chunk import ChunkSettings, chunk_document
from hr_navigator.ingestion.corpus import DEFAULT_MANIFEST, Document, load_corpus, manifest_hash
from hr_navigator.retrieval.bm25 import BM25, tokenize
from hr_navigator.retrieval.dense import DenseIndex, Embedder, EmbeddingUnavailable
from hr_navigator.safeguards.untrusted import injection_reason
from hr_navigator.schemas import Passage

log = logging.getLogger(__name__)


def index_text(doc: Document, text: str) -> str:
    header = " ".join(x for x in (doc.title, doc.org, " ".join(doc.org_aliases), doc.jurisdiction) if x)
    return f"{header}\n{text}"


@dataclass
class Index:
    settings: ChunkSettings
    version: str  # manifest hash + chunk settings; part of response cache keys
    docs: dict[str, Document]
    passages: list[Passage]
    texts: list[str]  # index_text for each passage (header + chunk text)
    flags: list[str | None]  # injection_reason for each passage
    bm25: BM25
    use_stem: bool = False
    dense: DenseIndex | None = None
    embedder: Embedder | None = None
    notes: list[str] = field(default_factory=list)


def build_index(manifest: str | Path = DEFAULT_MANIFEST, settings: ChunkSettings = ChunkSettings(), *,
                dense: str = "cached", embedder: Embedder | None = None, use_stem: bool = False,
                docs: list[Document] | None = None) -> Index:
    """``dense``: "off", "cached" (use vectors only if every chunk is already cached) or "build"
    (call the endpoint for missing vectors)."""
    source = "custom" if docs is not None else manifest_hash(manifest)
    docs = docs if docs is not None else load_corpus(manifest)
    passages, texts, flags = [], [], []
    for doc in docs:
        for c in chunk_document(doc, settings):
            passages.append(Passage(doc_id=doc.doc_id, chunk_id=c.chunk_id, text=c.text, title=doc.title,
                                    org=doc.org, jurisdiction=doc.jurisdiction, category=doc.category,
                                    url=doc.url, version_date=doc.version_date, synthetic=doc.synthetic))
            texts.append(index_text(doc, c.text))
            flags.append(injection_reason(c.text))
    version = f"{source}-{settings.name}"
    index = Index(settings=settings, version=version, docs={d.doc_id: d for d in docs}, passages=passages,
                  texts=texts, flags=flags, bm25=BM25([tokenize(t, use_stem=use_stem) for t in texts]),
                  use_stem=use_stem)
    flagged = sum(1 for f in flags if f)
    if flagged:
        index.notes.append(f"{flagged} chunk(s) look like instructions to the assistant and are never returned")
    if dense != "off":
        embedder = embedder or Embedder()
        try:
            index.dense = DenseIndex(embedder.embed(texts, cached_only=(dense == "cached")))
            index.embedder = embedder
        except EmbeddingUnavailable as exc:
            index.notes.append(f"dense search off: {exc}")
            if dense == "build":
                raise
    for note in index.notes:
        log.info("retrieval index: %s", note)
    return index

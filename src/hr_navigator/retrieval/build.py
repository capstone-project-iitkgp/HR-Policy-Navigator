"""Build the index once and report it; with --dense, embed every chunk into the disk cache.

    python -m hr_navigator.retrieval.build                 # chunks and BM25 only (offline)
    python -m hr_navigator.retrieval.build --dense         # needs EMBED_* in .env, Zscaler off

After a --dense run, search uses hybrid mode automatically (RETRIEVAL_MODE=auto). Vectors are
cached per chunk text, so a second run makes no calls. Do not run --dense while a full chat
evaluation is running on the shared key.
"""

from __future__ import annotations

import argparse
import time

from hr_navigator.ingestion.chunk import ChunkSettings, estimate_tokens
from hr_navigator.retrieval.index import build_index


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dense", action="store_true", help="embed missing chunks via the endpoint")
    ap.add_argument("--target", type=int, default=ChunkSettings.target)
    ap.add_argument("--overlap", type=int, default=ChunkSettings.overlap)
    ap.add_argument("--max-tokens", type=int, default=ChunkSettings.max_tokens)
    args = ap.parse_args(argv)
    settings = ChunkSettings(args.target, args.overlap, args.max_tokens)
    t0 = time.time()
    index = build_index(settings=settings, dense="build" if args.dense else "cached")
    tokens = [estimate_tokens(t) for t in index.texts]
    print(f"index {index.version}: {len(index.docs)} documents, {len(index.passages)} chunks, "
          f"estimated tokens per indexed chunk min/avg/max {min(tokens)}/{sum(tokens) // len(tokens)}/{max(tokens)}")
    print(f"dense: {'on' if index.dense is not None else 'off'}; built in {time.time() - t0:.1f} s")
    cut = getattr(index.embedder, "truncated", 0)
    if cut:
        print(f"note: {cut} chunk(s) were over the 512-token embedding limit; only their first part was embedded")
    for note in index.notes:
        print("note:", note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

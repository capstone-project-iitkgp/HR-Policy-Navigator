"""Split a document into overlapping chunks that are exact slices of its text.

    chunks = chunk_document(doc, ChunkSettings(target=300, overlap=50))
    chunks[0].text == doc.text[chunks[0].start:chunks[0].end]   # always true

Rules:
- A chunk is built from whole lines until it reaches ``target`` estimated tokens. A single line
  longer than ``max_tokens`` is split at spaces.
- The next chunk starts with the last lines of the previous one, about ``overlap`` tokens.
- Text is never changed (no lower-casing, no whitespace edits): the agent checks the model's
  quotes against it. Leading and trailing blank space is left out of the slice.
- ``chunk_id`` is ``<doc_id>-<4-digit index>``. It is stable while the text and settings stay
  the same; the index build records the settings so a change is visible.

Not verified: the token estimate. It counts words and punctuation marks times 1.3, which is
close to WordPiece (bge-small) on English prose but was not checked with the real tokenizer.
The default (target 300, max 400) leaves room under the embeddings endpoint's 512-token limit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hr_navigator.ingestion.corpus import Document

_PIECE = re.compile(r"\w+|[^\w\s]")
MIN_TOKENS = 15  # chunks smaller than this (page footers, blank pages) are dropped


def estimate_tokens(text: str) -> int:
    return int(len(_PIECE.findall(text)) * 1.3 + 0.5)


@dataclass(frozen=True)
class ChunkSettings:
    target: int = 300
    overlap: int = 50
    max_tokens: int = 400

    @property
    def name(self) -> str:
        return f"t{self.target}o{self.overlap}"


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    chunk_id: str
    start: int  # character offsets into the document text
    end: int
    text: str


def _lines(text: str) -> list[tuple[int, int]]:
    """(start, end) of every line, the newline included in the end."""
    spans, pos = [], 0
    for line in text.splitlines(keepends=True):
        spans.append((pos, pos + len(line)))
        pos += len(line)
    return spans


def _split_long(text: str, start: int, end: int, max_tokens: int) -> list[tuple[int, int]]:
    """Split one over-long line at spaces into pieces of at most ``max_tokens``."""
    pieces, piece_start = [], start
    for m in re.finditer(r"\S+\s*", text[start:end]):
        cut = start + m.end()
        if estimate_tokens(text[piece_start:cut]) > max_tokens and cut > piece_start:
            prev = start + m.start()
            if prev > piece_start:
                pieces.append((piece_start, prev))
                piece_start = prev
    pieces.append((piece_start, end))
    return pieces


def _units(text: str, max_tokens: int) -> list[tuple[int, int, int]]:
    """Lines (long ones split) as (start, end, tokens)."""
    units = []
    for s, e in _lines(text):
        for ps, pe in ([(s, e)] if estimate_tokens(text[s:e]) <= max_tokens else _split_long(text, s, e, max_tokens)):
            units.append((ps, pe, estimate_tokens(text[ps:pe])))
    return units


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def chunk_document(doc: Document, settings: ChunkSettings = ChunkSettings()) -> list[Chunk]:
    text, units = doc.text, _units(doc.text, settings.max_tokens)
    chunks: list[Chunk] = []
    i = 0
    while i < len(units):
        j, tokens = i, 0
        while j < len(units) and (tokens == 0 or tokens + units[j][2] <= settings.max_tokens):
            tokens += units[j][2]
            j += 1
            if tokens >= settings.target:
                break
        start, end = _trim(text, units[i][0], units[j - 1][1])
        if estimate_tokens(text[start:end]) >= MIN_TOKENS:
            chunks.append(Chunk(doc.doc_id, f"{doc.doc_id}-{len(chunks):04d}", start, end, text[start:end]))
        if j >= len(units):
            break
        # step back over whole lines worth about `overlap` tokens, but always move forward
        back, k = 0, j
        while k - 1 > i and back + units[k - 1][2] <= settings.overlap:
            k -= 1
            back += units[k][2]
        i = k
    return chunks

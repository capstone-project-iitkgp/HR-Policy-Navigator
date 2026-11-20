"""Corpus manifest loading and the chunker (A2, A3)."""

import csv
import hashlib
import json
from pathlib import Path

import pytest

from hr_navigator.ingestion.chunk import ChunkSettings, chunk_document, estimate_tokens
from hr_navigator.ingestion.corpus import DEFAULT_MANIFEST, CorpusError, Document, load_corpus

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ["doc_id", "title", "org", "org_aliases", "jurisdiction", "category", "url", "version_date",
          "retrieved", "text_path", "text_sha256", "synthetic", "source_note"]


def write_manifest(tmp_path, rows):
    path = tmp_path / "m.csv"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({f: r.get(f, "") for f in FIELDS})
    return path


def text_row(tmp_path, doc_id, text, **extra):
    (tmp_path / f"{doc_id}.txt").write_text(text)
    return dict(doc_id=doc_id, title=f"T {doc_id}", org="Org", text_path=f"{doc_id}.txt",
                text_sha256=hashlib.sha256(text.encode()).hexdigest(), synthetic="false", **extra)


# ---- manifest -------------------------------------------------------------------
def test_real_manifest_loads_and_matches_eval_sources():
    docs = load_corpus()
    assert [d.doc_id for d in docs] == ["DOC-001", "DOC-002", "DOC-003", "DOC-004", "DOC-005", "DOC-006",
                                        "DOC-008", "DOC-009"]
    assert all(d.org and d.title and d.text for d in docs)


def test_every_gold_source_in_the_eval_set_is_in_the_corpus():
    ids = {d.doc_id for d in load_corpus()}
    for line in (ROOT / "data/eval/eval_v1.jsonl").read_text().splitlines():
        for g in json.loads(line)["gold_sources"]:
            assert g["doc_id"] in ids


def test_aliases_and_bool_parse(tmp_path):
    m = write_manifest(tmp_path, [text_row(tmp_path, "D1", "hello", org_aliases="A; B")])
    doc = load_corpus(m, root=tmp_path)[0]
    assert doc.org_aliases == ("A", "B") and doc.synthetic is False
    assert doc.org_names() == ("org", "a", "b")


@pytest.mark.parametrize("change, message", [
    (lambda r: r.update(text_sha256="0" * 64), "text_sha256"),
    (lambda r: r.update(org=""), "missing org"),
    (lambda r: r.update(text_path="nope.txt"), "not found"),
    (lambda r: r.update(synthetic="yes"), "synthetic"),
])
def test_bad_rows_fail_loudly(tmp_path, change, message):
    row = text_row(tmp_path, "D1", "hello")
    change(row)
    with pytest.raises(CorpusError, match=message):
        load_corpus(write_manifest(tmp_path, [row]), root=tmp_path)


def test_duplicate_doc_id_fails(tmp_path):
    row = text_row(tmp_path, "D1", "hello")
    with pytest.raises(CorpusError, match="duplicate"):
        load_corpus(write_manifest(tmp_path, [row, dict(row)]), root=tmp_path)


# ---- chunker --------------------------------------------------------------------
def doc(text, doc_id="D1"):
    return Document(doc_id=doc_id, title="T", org="O", text=text)


LINES = "\n".join(f"Line {i}: employees are entitled to {i} days of casual leave per year." for i in range(200))


def test_chunks_are_exact_slices_within_the_size_limit():
    settings = ChunkSettings(target=100, overlap=20, max_tokens=140)
    d = doc(LINES)
    chunks = chunk_document(d, settings)
    assert len(chunks) > 5
    for c in chunks:
        assert c.text == d.text[c.start:c.end]
        assert c.text == c.text.strip()
        assert estimate_tokens(c.text) <= settings.max_tokens


def test_chunks_overlap_and_cover_the_document():
    d = doc(LINES)
    chunks = chunk_document(d, ChunkSettings(target=100, overlap=20, max_tokens=140))
    for a, b in zip(chunks, chunks[1:]):
        assert b.start < a.end  # overlap
        assert b.start > a.start  # always moves forward
    assert chunks[0].start == 0 and chunks[-1].end == len(d.text)


def test_chunk_ids_are_unique_and_stable():
    first = chunk_document(doc(LINES))
    again = chunk_document(doc(LINES))
    assert [c.chunk_id for c in first] == [c.chunk_id for c in again]
    assert len({c.chunk_id for c in first}) == len(first)
    assert first[0].chunk_id == "D1-0000"


def test_a_short_document_is_one_chunk_and_blank_text_is_none():
    assert len(chunk_document(doc("Casual leave is twelve days a year for every permanent employee here."))) == 1
    assert chunk_document(doc("   \n\n  \n")) == []


def test_one_very_long_line_is_split_at_spaces():
    d = doc(" ".join(["word"] * 2000))
    chunks = chunk_document(d, ChunkSettings(target=200, overlap=0, max_tokens=250))
    assert len(chunks) > 5 and all(estimate_tokens(c.text) <= 250 for c in chunks)
    assert all(c.text == d.text[c.start:c.end] for c in chunks)


def test_real_corpus_chunks_fit_the_agent_and_embedding_limits():
    for d in load_corpus(DEFAULT_MANIFEST):
        for c in chunk_document(d):
            assert c.text == d.text[c.start:c.end]
            assert estimate_tokens(c.text) <= 400

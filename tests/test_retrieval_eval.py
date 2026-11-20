"""recall@k harness on a hand-made example with known answers (A5)."""

import pytest

from evaluation.retrieval_eval import evaluate, norm
from hr_navigator.ingestion.chunk import ChunkSettings
from hr_navigator.ingestion.corpus import Document
from hr_navigator.retrieval.index import build_index

DOCS = [
    Document("A", "Alpha Leave", "Alpha Corp",
             "Casual leave is 12 days per year for every permanent employee of the company.\n"),
    Document("B", "Beta Travel", "Beta Uni",
             "Mileage is paid at 45p per mile when staff use their own car for approved trips.\n"),
]


def item(qid, q, gold, quotes, org=None):
    return {"qid": qid, "type": "lookup", "question": q, "gold_sources": [{"doc_id": gold}],
            "evidence_quotes": quotes, "employee_context": {"org": org} if org else None}


def test_metrics_on_known_answers():
    idx = build_index(docs=DOCS, settings=ChunkSettings(50, 0, 80), dense="off")
    items = [
        item("Q1", "casual leave days", "A", ["Casual leave is  12 DAYS per year"]),  # hit at rank 1
        item("Q2", "mileage per mile", "A", ["not in any chunk"]),  # wrong doc first, gold second
        item("Q3", "xylophone", "B", ["Mileage is paid"]),  # nothing found
    ]
    m, rows = evaluate(items, idx, "bm25", org="none")
    assert m["n"] == 3
    assert m["doc_recall@1"] == pytest.approx(1 / 3, abs=1e-3)
    assert m["evidence_recall@1"] == pytest.approx(1 / 3, abs=1e-3)
    assert m["evidence_reachable"] == pytest.approx(2 / 3, abs=1e-3)
    assert [r["first_gold_rank"] for r in rows] == [1, 2, None]
    assert m["mrr"] == pytest.approx((1 + 1 / 2) / 3, abs=1e-3)


def test_org_filter_is_taken_from_the_employee_profile():
    idx = build_index(docs=DOCS, settings=ChunkSettings(50, 0, 80), dense="off")
    m, rows = evaluate([item("Q1", "leave mileage", "B", [], org="Beta Uni")], idx, "bm25", org="profile")
    assert rows[0]["top"] == ["B"] and m["doc_recall@1"] == 1.0


def test_norm_ignores_case_whitespace_and_curly_quotes():
    assert norm("  It’s   TWELVE\ndays ") == "it's twelve days"

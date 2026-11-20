"""recall@k for retrieval alone, against the frozen eval set (A5).

    python -m evaluation.retrieval_eval                    # offline: BM25 variants
    python -m evaluation.retrieval_eval --modes bm25 hybrid dense   # after retrieval.build --dense

For every eval item with ``gold_sources`` the raw question is the query. Metrics per
(chunk setting, mode, stemming, org filter):
- doc_recall@k: a gold doc_id is among the top k passages.
- evidence_recall@k: one of the item's ``evidence_quotes`` appears inside a top-k passage
  (case and whitespace ignored).
- mrr: mean of 1 / rank of the first passage from a gold document (0 if not in the top 10).
- evidence_reachable: share of items whose quote exists in at least one chunk at all; evidence
  recall cannot be higher than this (a quote can be cut by a chunk boundary or a page footer).

"org" says where the organisation comes from: "none" (search ignores organisations), "query"
(search keeps to organisations the question names; the default in engine.search) or "profile"
(``filters={"org": <employee_context.org>}``, the way the agent could pass it from the employee
profile; falls back to "query" when the item has no organisation).

Writes evaluation/retrieval_results.csv and evaluation/retrieval_results.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import defaultdict
from pathlib import Path

from hr_navigator.ingestion.chunk import ChunkSettings
from hr_navigator.ingestion.corpus import load_corpus
from hr_navigator.retrieval.engine import search
from hr_navigator.retrieval.index import Index, build_index

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "data" / "eval" / "eval_v1.jsonl"
OUT_CSV = ROOT / "evaluation" / "retrieval_results.csv"
OUT_MD = ROOT / "evaluation" / "retrieval_results.md"
KS = (1, 3, 5, 10)
SETTINGS = (ChunkSettings(200, 40, 300), ChunkSettings(300, 50, 400), ChunkSettings(400, 60, 480))


def norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def load_items(path: Path = EVAL) -> list[dict]:
    items = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [i for i in items if i.get("gold_sources")]


def item_org(item: dict) -> str | None:
    return ((item.get("employee_context") or {}).get("org") or None)


def evaluate(items: list[dict], index: Index, mode: str, org: str = "query") -> tuple[dict, list[dict]]:
    """Returns (metrics, per-item rows)."""
    texts = [norm(p.text) for p in index.passages]
    rows = []
    for item in items:
        gold = {g["doc_id"] for g in item["gold_sources"]}
        quotes = [norm(q) for q in item.get("evidence_quotes") or [] if q.strip()]
        profile_org = item_org(item) if org == "profile" else None
        results = search(item["question"], k=max(KS), filters={"org": profile_org} if profile_org else None,
                         mode=mode, index=index, org_from_query=(org != "none"))
        ranks = [r + 1 for r, p in enumerate(results) if p.doc_id in gold]
        ev_ranks = [r + 1 for r, p in enumerate(results) if any(q in norm(p.text) for q in quotes)]
        rows.append({
            "qid": item["qid"], "type": item["type"], "gold": sorted(gold),
            "first_gold_rank": ranks[0] if ranks else None,
            "first_evidence_rank": ev_ranks[0] if ev_ranks else None,
            "evidence_reachable": bool(quotes) and any(q in t for q in quotes for t in texts),
            "has_quotes": bool(quotes), "top": [p.doc_id for p in results[:3]],
        })
    n = len(rows)
    with_q = [r for r in rows if r["has_quotes"]]
    m = {"n": n, "n_with_quotes": len(with_q),
         "mrr": round(sum(1 / r["first_gold_rank"] for r in rows if r["first_gold_rank"]) / n, 3),
         "evidence_reachable": round(sum(r["evidence_reachable"] for r in with_q) / max(len(with_q), 1), 3)}
    for k in KS:
        m[f"doc_recall@{k}"] = round(sum(1 for r in rows if r["first_gold_rank"] and r["first_gold_rank"] <= k) / n, 3)
        m[f"evidence_recall@{k}"] = round(
            sum(1 for r in with_q if r["first_evidence_rank"] and r["first_evidence_rank"] <= k) / max(len(with_q), 1), 3)
    return m, rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="recall@k for retrieval")
    ap.add_argument("--modes", nargs="+", default=["bm25"])
    ap.add_argument("--out-csv", type=Path, default=OUT_CSV)
    ap.add_argument("--out-md", type=Path, default=OUT_MD)
    args = ap.parse_args(argv)
    items, docs = load_items(), load_corpus()
    t0 = time.time()
    table, details = [], {}
    for settings in SETTINGS:
        for use_stem in (True, False):
            index = build_index(settings=settings, docs=docs, use_stem=use_stem,
                                dense="cached" if set(args.modes) - {"bm25"} else "off")
            for mode in args.modes:
                if mode != "bm25" and index.dense is None:
                    print(f"skip {mode} at {settings.name}: no cached embeddings")
                    continue
                for org in ("none", "query", "profile"):
                    m, rows = evaluate(items, index, mode, org)
                    key = dict(chunking=settings.name, mode=mode, stem=use_stem, org=org)
                    table.append({**key, **m})
                    details[tuple(key.values())] = rows
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(table[0]))
        w.writeheader()
        w.writerows(table)

    best = max(table, key=lambda r: (r["doc_recall@5"], r["evidence_recall@5"], r["mrr"]))
    rows = details[(best["chunking"], best["mode"], best["stem"], best["org"])]
    by_type = defaultdict(list)
    for r in rows:
        by_type[r["type"]].append(bool(r["first_gold_rank"] and r["first_gold_rank"] <= 5))
    misses = [r for r in rows if not (r["first_gold_rank"] and r["first_gold_rank"] <= 5)]
    lines = [
        "# Retrieval results (recall@k)",
        "",
        f"Generated by `python -m evaluation.retrieval_eval --modes {' '.join(args.modes)}` on the frozen "
        f"eval_v1 ({len(items)} items with gold sources; {best['n_with_quotes']} with evidence quotes). "
        f"Corpus: {len(docs)} documents. Run time {time.time() - t0:.1f} s. Full table: `retrieval_results.csv`.",
        "",
        "| chunking | mode | stem | org | doc R@1 | doc R@5 | doc R@10 | evid R@5 | evid reachable | MRR |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in table:
        lines.append(f"| {r['chunking']} | {r['mode']} | {r['stem']} | {r['org']} | {r['doc_recall@1']} | "
                     f"{r['doc_recall@5']} | {r['doc_recall@10']} | {r['evidence_recall@5']} | "
                     f"{r['evidence_reachable']} | {r['mrr']} |")
    lines += ["", f"**Best by doc recall@5:** chunking {best['chunking']}, mode {best['mode']}, stem {best['stem']}, "
              f"org {best['org']}.", "", "doc recall@5 by question type (best setting):", ""]
    for t, hits in sorted(by_type.items()):
        lines.append(f"- {t}: {sum(hits)}/{len(hits)}")
    lines += ["", f"Misses at k=5 (best setting): {len(misses)}", ""]
    for r in misses[:10]:
        lines.append(f"- {r['qid']} ({r['type']}): gold {', '.join(r['gold'])}; top 3 were {', '.join(r['top']) or 'nothing'}")
    args.out_md.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

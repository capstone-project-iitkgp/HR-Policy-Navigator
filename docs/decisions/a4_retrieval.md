# A2 to A5: corpus, chunking, retrieval and recall@k (Akash)

Status 20 Nov 2026: built and tested offline (BM25). Dense and hybrid modes are built and unit-tested
with a fake embedder, but **not yet run against the real embeddings endpoint** (no key or CA file on
this laptop). Claude drafted the code; Akash reviews (see `docs/ai_use_log.md`).

## What the agent gets

`from hr_navigator.retrieval import search` and `search(query, k=5, filters=None) -> list[Passage]`,
as asked in `docs/for_akash_status_20nov.html`:

| Himadri's point (2.2) | Answer |
|---|---|
| Stable, unique `chunk_id` | `<doc_id>-<4-digit index>`, e.g. `DOC-008-0031`. Same text and settings give the same ids (tested). Changing the chunk settings changes them; the index version string records the settings. |
| Unmodified `text` | Every chunk is an exact slice of the source text (tested for every chunk of the real corpus). Only leading and trailing blank space is left out. |
| About 500 tokens or fewer | Default target 300, maximum 400 estimated tokens (590 chunks, average 309). |
| `org`, `jurisdiction` | Filled from the manifest for every passage. `title` is the document title. |
| `synthetic`, `url`, `scores` | Filled. `version_date` is empty for all 8 documents (no clean effective date in the sources). |
| May return `[]`, may raise | Returns `[]` for an empty query or no match. Raises only for a programming error (unknown filter key, unknown mode); a failing embeddings call falls back to BM25 instead of raising. |

## Decisions

1. **Corpus = the 8 eval source documents** (`data/manifests/corpus_v1.csv`, built from
   `data/eval/source_manifest.csv`; every text hash checked on load). The text is already in git,
   so a fresh clone needs no download. The downloader and the 30 seed URLs are not on this laptop;
   growing the corpus is still open (A2), and the report must give the real count: 8.
2. **Chunker:** whole lines up to a token target, with about 50 tokens of line overlap. Token
   counts are estimated (words and punctuation x 1.3), not measured with the bge tokenizer.
3. **BM25 written out in full** (`retrieval/bm25.py`, about 50 lines) instead of `rank_bm25`: no new
   dependency, easy to explain. Stemming is available but off (see recall@k). Each chunk is ranked with a short header
   (title, organisation, aliases, jurisdiction) in front, so "DEF" or "Kentucky" in a question finds
   that organisation. The header is not part of `Passage.text`.
4. **Dense:** NumPy cosine over cached bge-small vectors (`.cache/embeddings/`, one file per text).
   `python -m hr_navigator.retrieval.build --dense` fills the cache; after that `auto` mode is hybrid.
   Search never embeds the whole corpus during an agent run.
5. **Fusion:** Reciprocal Rank Fusion, k = 60, top 50 from each ranker.
6. **No reranker yet.** It needs a new dependency (fastembed or sentence-transformers) and a model
   download inside Docker. Last item on the cut list; the report must say if it is dropped.
7. **Injection filter on retrieved text** (`safeguards/untrusted.py`): a passage that reads like an
   order to the assistant is dropped, never edited. Flags nothing in the real corpus.
8. **Index is rebuilt at start-up** (0.1 s) instead of being stored, so it cannot go stale.

## recall@k (BM25, frozen eval_v1, 66 items with gold sources)

Full table: `evaluation/retrieval_results.md` (`python -m evaluation.retrieval_eval`, 2 s).

Default chunking 300/50, no stemming (the shipped default):

| Organisation taken from | doc R@1 | doc R@5 | evidence R@5 | MRR |
|---|---|---|---|---|
| nowhere | 0.712 | 0.970 | 0.727 | 0.816 |
| the question (default in `search`) | 0.924 | 0.970 | 0.788 | 0.942 |
| the employee profile (`filters={"org": ...}`) | 1.0 | 1.0 | 0.788 | 1.0 |

Read with care: with only 8 documents, finding the right *document* is easy. Evidence recall
(the exact quote is inside a top-5 passage) is the useful number. The eval questions were written
from these same documents, so the numbers are optimistic for a larger corpus. Chunking 400/60 reaches
evidence R@5 0.803 but gives chunks near 480 estimated tokens; 300/50 was kept for headroom under the
512-token embedding limit and the agent's 8192-token prompt. Stemming changed evidence R@5 by 2 items
of 66 (worse), so it is off.

**Organisation from the question.** Plain BM25 gives an organisation's name little weight: "DEF
probation period length" ranked BLDEDU's probation rules first. So when no `org` filter is given and
the query names a corpus organisation (full name or alias, whole words), `search` keeps to it.

**Recommendation to Himadri (ask 3): pass `filters={"org": <employee org>}`** from the profile. It is
the best row above, and it matters for the agent: the planner's short queries ("probation period
length") rarely name the organisation, and without it that query returns only BLDEDU passages to a
DEF employee. Several organisations can be passed with ";". For an organisation that is not in the
corpus the filter returns `[]`, which the agent already turns into "the documents do not say".

Known miss: dev items D01 and D06 (DEF probation) with the raw question. Filler words ("joined last
week") outrank "probation". A short query with the org filter finds it first. Dense search should
help; not measured yet.

## Not verified

- Real embeddings endpoint: response shape, speed, limits (run `tests/test_embed_live.py`, then
  `retrieval.build --dense`, then `retrieval_eval --modes bm25 dense hybrid`).
- Token estimate against the real tokenizer.
- Behaviour on a larger corpus.

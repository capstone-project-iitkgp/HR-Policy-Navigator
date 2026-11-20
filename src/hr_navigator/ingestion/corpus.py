"""Load the policy corpus from its manifest.

    docs = load_corpus()            # data/manifests/corpus_v1.csv by default
    docs[0].doc_id, docs[0].text    # "DOC-001", the full extracted text

The manifest has one row per document. ``text_path`` points to the extracted text (relative to
the repo root). ``text_sha256`` is checked on load, so a changed text file fails loudly instead of
silently changing chunk ids and citations.

Not verified: ``version_date`` is empty for every current document (the sources do not state a
clean effective date); ``org_aliases`` were written by hand from the eval set's employee profiles.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = ROOT / "data" / "manifests" / "corpus_v1.csv"
REQUIRED = ("doc_id", "title", "org", "text_path", "text_sha256")


class CorpusError(ValueError):
    """The manifest or a text file is not usable."""


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    org: str
    text: str = field(repr=False)
    org_aliases: tuple[str, ...] = ()
    jurisdiction: str = ""
    category: str = ""
    url: str = ""
    version_date: str = ""
    synthetic: bool = False

    def org_names(self) -> tuple[str, ...]:
        """Full name plus aliases, lower case, for filtering."""
        return tuple(n.lower() for n in (self.org, *self.org_aliases) if n)


def _bool(value: str, doc_id: str) -> bool:
    v = (value or "false").strip().lower()
    if v not in ("true", "false"):
        raise CorpusError(f"{doc_id}: synthetic must be true or false, got {value!r}")
    return v == "true"


def load_corpus(manifest: str | Path = DEFAULT_MANIFEST, root: Path = ROOT) -> list[Document]:
    """Read every manifest row, check its hash and return the documents in manifest order."""
    manifest = Path(manifest)
    with manifest.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    docs: list[Document] = []
    seen: set[str] = set()
    for n, row in enumerate(rows, start=2):  # line 1 is the header
        missing = [k for k in REQUIRED if not (row.get(k) or "").strip()]
        if missing:
            raise CorpusError(f"{manifest.name} line {n}: missing {', '.join(missing)}")
        doc_id = row["doc_id"].strip()
        if doc_id in seen:
            raise CorpusError(f"{manifest.name} line {n}: duplicate doc_id {doc_id}")
        seen.add(doc_id)
        path = root / row["text_path"].strip()
        if not path.is_file():
            raise CorpusError(f"{doc_id}: text file not found: {row['text_path']}")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["text_sha256"].strip():
            raise CorpusError(f"{doc_id}: text_sha256 does not match {row['text_path']}")
        docs.append(Document(
            doc_id=doc_id,
            title=row["title"].strip(),
            org=row["org"].strip(),
            text=raw.decode("utf-8"),
            org_aliases=tuple(a.strip() for a in (row.get("org_aliases") or "").split(";") if a.strip()),
            jurisdiction=(row.get("jurisdiction") or "").strip(),
            category=(row.get("category") or "").strip(),
            url=(row.get("url") or "").strip(),
            version_date=(row.get("version_date") or "").strip(),
            synthetic=_bool(row.get("synthetic", ""), doc_id),
        ))
    return docs


def manifest_hash(manifest: str | Path = DEFAULT_MANIFEST) -> str:
    """Short hash of the manifest file; part of every cache key that depends on the corpus."""
    return hashlib.sha256(Path(manifest).read_bytes()).hexdigest()[:12]

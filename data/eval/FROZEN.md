# Eval sets frozen 2026-11-20

- `eval_v1.jsonl`: 80 items, SHA-256 `25cf3595de015925327ecfb62fa65a883c29b579538fcc8a0ffecf5678b40af0`
  - authorship status: {'DRAFT': 80}; items with a named human reviewer: 0
- `hrms_ext_v1.jsonl`: 20 items, SHA-256 `a6260a20b5dc1e04eb655a0cd648a8d5dfd4f75bb50197888ec4f9854338ea60`
  - authorship status: {'DRAFT': 20}; items with a named human reviewer: 0

**Human review pending.** Frozen on 20 Nov 2026 so the baselines and later runs use fixed questions.
Every result table must say the gold answers were AI-drafted and not yet human-verified, until the reviewers (Sakshi, Srinivas, Himadri) are recorded in each item's `authorship`.
A later change is a new version (v1.1) with a changelog, not an edit to these files.

Tag after committing: `git tag eval-v1`

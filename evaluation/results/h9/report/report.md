# H9 results

Records: 920 (920 not mock). Scorer: evaluation/score.py (rule-based, no model judge).

## Does the agent beat plain RAG?

no reliable difference between V2 (RAG) and V3 (agent): strict pass 0.15 (V2) vs 0.14 (V3) on 80 questions, McNemar exact p=1.000. Read with the cost table: V3 spends more model calls and time.

## eval_v1

### Headline

| Variant | runs | Pass strict (95% CI) | Pass lenient | SD across repeats | any@5 | MRR |
|---|---|---|---|---|---|---|
| V0 single prompt | 240 | 0.16 (0.12-0.21) | 0.17 | 0.000 | 0.00 | 0.00 |
| V0 few-shot | 240 | 0.18 (0.14-0.24) | 0.18 | 0.007 | 0.00 | 0.00 |
| V0 chain of thought | 240 | 0.19 (0.14-0.24) | 0.19 | 0.022 | 0.00 | 0.00 |
| V2 RAG | 80 | 0.15 (0.09-0.24) | 0.15 | n/a | 0.90 | 0.70 |
| V3 agent (critic advisory) | 80 | 0.14 (0.08-0.23) | 0.14 | n/a | 0.44 | 0.38 |

### Pass by question type (passed/runs)

| type | v0_single | v0_fewshot | v0_cot | v2_rag | v3_agent |
|---|---|---|---|---|---|
| ambiguous | 0/18 | 0/18 | 2/18 | 0/6 | 0/6 |
| conflicting | 0/12 | 0/12 | 2/12 | 0/4 | 0/4 |
| eligibility | 0/36 | 0/36 | 0/36 | 0/12 | 0/12 |
| injection | 6/18 | 5/18 | 2/18 | 0/6 | 2/6 |
| lookup | 0/60 | 0/60 | 0/60 | 2/20 | 1/20 |
| pii_overreach | 12/18 | 15/18 | 17/18 | 1/6 | 4/6 |
| procedural | 0/24 | 0/24 | 0/24 | 1/8 | 1/8 |
| synthesis | 0/30 | 0/30 | 0/30 | 0/10 | 0/10 |
| unanswerable | 21/24 | 24/24 | 22/24 | 8/8 | 3/8 |

### Failure labels (SUGGESTED, a human confirms in taxonomy_sheet.csv)

| Variant | runs | passed | tool_call_error | retrieval_miss | hallucination | reasoning_error |
|---|---|---|---|---|---|---|
| V0 single prompt | 240 | 39 | 0 | 0 | 201 | 0 |
| V0 few-shot | 240 | 44 | 0 | 0 | 196 | 0 |
| V0 chain of thought | 240 | 45 | 0 | 0 | 195 | 0 |
| V2 RAG | 80 | 12 | 0 | 6 | 8 | 54 |
| V3 agent (critic advisory) | 80 | 11 | 27 | 6 | 23 | 13 |

### Latency and cost

| Variant | median s | p95 s | mean s | tokens in/q | tokens out/q | model calls/q | tool calls/q | steps/q | cache hits |
|---|---|---|---|---|---|---|---|---|---|
| V0 single prompt | 1.6 | 5.6 | 2.3 | 244 | 101 | n/a | 0.00 | 1.0 | n/a |
| V0 few-shot | 1.0 | 2.8 | 1.3 | 688 | 55 | n/a | 0.00 | 1.0 | n/a |
| V0 chain of thought | 3.3 | 5.4 | 3.5 | 653 | 152 | n/a | 0.00 | 2.0 | n/a |
| V2 RAG | 2.9 | 8.6 | 3.5 | 2927 | 140 | 1.2 | 2.20 | 1.2 | 0/96 |
| V3 agent (critic advisory) | 7.4 | 11.2 | 6.4 | 9081 | 230 | 5.4 | 3.86 | 7.2 | 0/430 |

### Checks and agent behaviour

| Variant | runs | answers blocked | reported only | escalated | revisions/q | critic verdicts | top validator issues |
|---|---|---|---|---|---|---|---|
| V2 RAG | 80 | 0.00 | 0 | 0.00 | 0.20 | -=80 | citation=15 |
| V3 agent (critic advisory) | 80 | 0.00 | 0 | 0.00 | 0.05 | -=40, ok=30, revise=10 | citation=12 |

### Paired comparisons (ablations are deltas against V3)

- v0_cot vs v2_rag on 80 shared questions: v0_cot-only pass 6, v2_rag-only pass 4, McNemar exact p=0.7539; mean diff (v2_rag-v0_cot) 95% bootstrap CI -0.113..+0.033
- v2_rag vs v3_agent on 80 shared questions: v2_rag-only pass 6, v3_agent-only pass 5, McNemar exact p=1.0000; mean diff (v3_agent-v2_rag) 95% bootstrap CI -0.100..+0.062

## hrms_ext_v1

### Headline

| Variant | runs | Pass strict (95% CI) | Pass lenient | SD across repeats | any@5 | MRR |
|---|---|---|---|---|---|---|
| V2 RAG | 20 | 0.05 (0.01-0.24) | 0.05 | n/a | 0.92 | 0.64 |
| V3 agent (critic advisory) | 20 | 0.20 (0.08-0.42) | 0.30 | n/a | 0.31 | 0.18 |

### Pass by question type (passed/runs)

| type | v2_rag | v3_agent |
|---|---|---|
| action_blocked | 0/2 | 1/2 |
| action_draft | 0/2 | 1/2 |
| ambiguous | 1/1 | 0/1 |
| calculation | 0/2 | 0/2 |
| db_lookup | 0/3 | 0/3 |
| eligibility_db | 0/5 | 0/5 |
| injection | 0/1 | 0/1 |
| over_reach | 0/2 | 2/2 |
| policy_plus_db | 0/2 | 0/2 |

### Failure labels (SUGGESTED, a human confirms in taxonomy_sheet.csv)

| Variant | runs | passed | tool_call_error | retrieval_miss | hallucination | reasoning_error |
|---|---|---|---|---|---|---|
| V2 RAG | 20 | 1 | 17 | 0 | 2 | 0 |
| V3 agent (critic advisory) | 20 | 4 | 14 | 0 | 1 | 1 |

### Latency and cost

| Variant | median s | p95 s | mean s | tokens in/q | tokens out/q | model calls/q | tool calls/q | steps/q | cache hits |
|---|---|---|---|---|---|---|---|---|---|
| V2 RAG | 2.1 | 6.4 | 2.8 | 3275 | 105 | 1.3 | 2.65 | 1.3 | 0/26 |
| V3 agent (critic advisory) | 6.6 | 11.6 | 6.3 | 9758 | 213 | 6.0 | 4.50 | 8.1 | 0/119 |

### Checks and agent behaviour

| Variant | runs | answers blocked | reported only | escalated | revisions/q | critic verdicts | top validator issues |
|---|---|---|---|---|---|---|---|
| V2 RAG | 20 | 0.00 | 0 | 0.35 | 0.30 | -=20 | citation=2, number=1, draft_claim=1 |
| V3 agent (critic advisory) | 20 | 0.00 | 0 | 0.45 | 0.00 | ok=12, -=8 | number=1 |

### HRMS tool use

| Variant | runs | pass | right tools | right arguments | forbidden tool calls | wrong state |
|---|---|---|---|---|---|---|
| V2 RAG | 20 | 0.05 | 0.15 | 0.15 | 0 | 2 |
| V3 agent (critic advisory) | 20 | 0.20 | 0.60 | 0.45 | 2 | 1 |

### Paired comparisons (ablations are deltas against V3)

- v2_rag vs v3_agent on 20 shared questions: v2_rag-only pass 1, v3_agent-only pass 4, McNemar exact p=0.3750; mean diff (v3_agent-v2_rag) 95% bootstrap CI -0.050..+0.350

## Reading notes

- Strict pass is the figure to report; lenient counts borderline fact matches and is an upper bound.
- Failure labels and `needs_human.csv` rows are unconfirmed until a person has gone through the sheets.
- The eval sets are DRAFT until the human review of the answers is done (UNVERIFIED if finished).
- Ablations run with fewer repeats than the main variants; their intervals are wider.

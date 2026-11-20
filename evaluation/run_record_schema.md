# Run record format (input to score_eval.py)

One JSON object per line: one question x one variant x one repeat. Anyone running a pipeline (baseline, prompt-only, RAG, agentic) writes this file; the scorer reads it. Stdlib JSON only.

| Field | Type | Notes |
|---|---|---|
| qid | str | Must exist in eval_v1.jsonl |
| variant | str | `baseline`, `prompt_only`, `rag`, `agentic` (free text allowed) |
| repeat | int | 0,1,2 for the 3 repeats |
| answer_text | str | Final answer shown to the user (thinking text stripped) |
| citations | list | `{doc_id, section, quote}`; `quote` is the passage text the answer claims as support |
| retrieved | list | Top-k chunks IN RANK ORDER: `{chunk_id, doc_id, text}`; log what the tool returned, not what the LLM used |
| tool_calls | list | `{name, args, error}`; `error` null or a string |
| steps | int | Agent steps used (the brief caps this at 10) |
| latency_s, tokens_in, tokens_out | number | Per query, summed over all LLM calls |
| finish_reason | str | Last call's reason; `length` means truncated |
| json_valid | bool | Did every structured output validate (after retries)? |
| error | str/null | Unrecovered exception, loop abort, or step-cap abort |

Retrieval is scored by whether a chunk's text contains a gold `evidence_quotes` entry (normalised), so it survives re-chunking. Rows for unanswerable questions have no quotes and are skipped for retrieval metrics.
Do not put the API key or real personal data in run records.

# Decision: local stand-in LLM server (`tools/mock_llm`)

Date: 16 Nov 2026. Status: implemented.

## Problem
The shared course API allows 1 concurrent request per key, about 60 requests/min and 8192 tokens (1024 output). Five people develop and run evaluations against it, and it may expire on an unknown date. We want to develop and test without using that quota.

## Decision
A small proxy in front of a local OpenAI-compatible backend (LM Studio first; Ollama and llama.cpp also work). The model (Qwen3-8B or Llama 3.1 8B Instruct) is chosen at start with `--model`, or from a menu in a terminal.

## Why a proxy and not our own inference
- LM Studio, Ollama and llama.cpp already serve `/v1/chat/completions` with streaming and tool calls. Re-implementing that adds risk and no value.
- What those tools lack is what we need: a fixed model name (so client code and `.env` do not change), the course limits (so rate and concurrency bugs show up locally), and a request log.

## Why real models only
A scripted or canned mode was considered and rejected for now. Tests that need no model use their own fakes, as `tools/mock_llm/tests` does with an in-process backend. A scripted mode can be added later if fault-injection tests become important.

## Assumptions (UNVERIFIED against the real API)
- A second concurrent request returns 429 (it could instead queue). `--queue` switches this. VERIFIED 20 Nov: the real API returns 429 (`per-key concurrent request limit exceeded`), so the default is right. The real API sends no `Retry-After` header; the mock does, so the client's fixed backoff is what runs against the real endpoint.
- ~~`max_tokens` above 1024 is clamped~~: VERIFIED 20 Nov that the real API rejects it with HTTP 400; the mock now does the same.
- No embeddings endpoint. The 13 Nov embedding email may change this.
- Prompt size is estimated as characters / 4.
Re-check these in mentor session #2 or with a smoke test, then update `limits.py` and the README table.

## Constraints
- Qwen weights are not permitted on the office laptop. The `qwen` option exists for permitted machines and fails with a clear message when the model is not loaded.
- Local outputs are for plumbing only. Evaluation numbers must come from the real endpoint.
- The stand-in never reads or logs the real API key. It listens on localhost by default.

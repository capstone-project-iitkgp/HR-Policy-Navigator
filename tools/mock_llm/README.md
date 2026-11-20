# mock_llm: local stand-in for the course LLM endpoint

The shared team API (Qwen/Qwen3-8B) allows 1 concurrent request per key, about 60 requests/min and 8192 tokens, and it may expire at an unknown date. `mock_llm` lets you develop and test against a model running on your own machine, behind the same kind of OpenAI-compatible URL, so your code does not burn shared quota.

It is a thin proxy. A local backend (LM Studio, Ollama, llama.cpp) does the inference. `mock_llm` adds model selection, a fixed model name, emulation of the course limits, and a request log. It does not run a model itself and does not download anything.

```
your code  ->  mock_llm (127.0.0.1:8099/v1)  ->  LM Studio (localhost:1234/v1)  ->  local model
```

## Quickstart

1. **Install LM Studio and run it once.** The `lms` command ships with LM Studio but only works after the app has been opened at least once. Check with `lms --help`. Everything below can also be done in the LM Studio app (Discover tab to download, Developer tab to load and start the server).
2. **Download, load and serve the model.** Use the commands for the model you want (see the next section for both). They are run once per model, outside this repo.
3. **Start the stand-in** from the `Code/` folder:
   ```bash
   source .venv/bin/activate
   python -m tools.mock_llm                 # asks which model, in a terminal
   python -m tools.mock_llm --model llama   # or choose directly: llama | qwen
   ```
   It prints the lines to use:
   ```
   CHAT_BASE_URL=http://127.0.0.1:8099/v1
   CHAT_MODEL=meta-llama/Llama-3.1-8B-Instruct   (or Qwen/Qwen3-8B)
   API_KEY=dummy
   ```
4. Put them in your own `.env`. To go back to the real API, restore the real `CHAT_BASE_URL` and key. No code change is needed.

Quick check:
```bash
curl -s http://127.0.0.1:8099/health
curl -s http://127.0.0.1:8099/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"meta-llama/Llama-3.1-8B-Instruct","messages":[{"role":"user","content":"Say hi"}]}'
```

## LM Studio commands, per model

Commands come from LM Studio's CLI docs (`lms get`, `lms ls`, `lms load`, `lms ps`, `lms server start`). Model catalogue names are **UNVERIFIED**: `lms get` searches by name and may show several matches, so check that the one you pick is the 8B **Instruct** build (Llama) or Qwen3-**8B** (Qwen). About 5 GB of disk per model; 16 GB of RAM is comfortable.

### Llama 3.1 8B Instruct (use this on the office laptop)

```bash
# 1. Download (once). -a / --always-show-all-results lets you choose from the matches.
lms get llama-3.1-8b-instruct --mlx -a     # MLX build, fastest on Apple Silicon
#   If nothing matches, drop --mlx for a GGUF build, or use a quantisation tag:
#   lms get llama-3.1-8b@q4_k_m

# 2. See what is on disk and copy the model key of the Instruct build.
lms ls

# 3. Load it. 8192 matches the course context limit.
lms load <model_key> --context-length 8192

# 4. Confirm it is loaded, then start the local server (default http://localhost:1234).
lms ps
lms server start
```

### Qwen3-8B (only on a machine where Qwen is permitted)

```bash
lms get qwen3-8b --mlx -a                  # drop --mlx for a GGUF build
lms ls
lms load <model_key> --context-length 8192
lms ps
lms server start
```

### Other `lms` commands you may need

| Command | Purpose |
| --- | --- |
| `lms ps` | List models currently loaded in memory |
| `lms unload <model_key>` | Unload a model (do this before switching models, to free RAM) |
| `lms server status` / `lms server stop` | Check or stop the local server |
| `lms get --mlx` / `lms get --gguf` | Browse recommended models by format |

Then start the stand-in (step 3 above). If `lms` is not found, open the LM Studio app once and try again.

## Choosing the model

| `--model` | Served as (what clients see) | Backend id it looks for |
| --- | --- | --- |
| `llama` | `meta-llama/Llama-3.1-8B-Instruct` | an id containing `llama-3.1-8b` (prefers `instruct`) |
| `qwen` | `Qwen/Qwen3-8B` | an id containing `qwen3-8b` |

The model must already be loaded in the backend. If it is not, the script exits with code 4, lists what the backend does report, and (if the `lms` CLI is installed) prints how to load one. Requests that name a different model get 404, as a wrong model name would on the real API.

## Options

| Flag | Default | Meaning |
| --- | --- | --- |
| `--model {qwen,llama}` | ask | Model to serve. Required when not run in a terminal. |
| `--backend-url` | `http://localhost:1234/v1` | Any OpenAI-compatible backend. Ollama: `http://localhost:11434/v1`. |
| `--host`, `--port` | `127.0.0.1`, `8099` | Where the stand-in listens. Keep it on localhost. |
| `--no-limits` | off | Turn off all limit emulation (pure passthrough plus rename). |
| `--queue` | off | Queue concurrent requests instead of answering 429. |
| `--require-key` | off | Reject requests with no `Authorization: Bearer` header, to catch client bugs. Any value is accepted. |
| `--log-bodies` | off | Also log request and response bodies. They may contain sensitive text. |

Exit codes: 2 usage error (for example no `--model` without a terminal), 3 backend unreachable, 4 model not loaded.

## What it emulates

| Behaviour | Stand-in | Real API status |
| --- | --- | --- |
| 1 concurrent request | A second request while one is running gets 429 plus `Retry-After: 1` (or waits with `--queue`) | Limit reported by the course. **How** it is enforced (429 or queueing) is UNVERIFIED. |
| 60 requests/min | Sliding window, 429 plus `Retry-After` when exceeded | Number reported by the course; source unclear. |
| 8192 tokens input + output | 400 `context_length_exceeded` if estimated prompt + `max_tokens` exceeds it | Limit reported. The prompt size here is an **estimate** (characters / 4), not a real tokenizer count. |
| 1024 max output | `max_tokens` above 1024 is rejected with HTTP 400 (`output token limit must be between 1 and 1024`); a missing one defaults to 1024. The response carries an `X-Mock-LLM-Notes` header. | Limit reported; reject-not-clamp VERIFIED on the real API 20 Nov. |
| No embeddings endpoint | `/v1/embeddings` returns 404 | UNVERIFIED. The 13 Nov embedding email may change this. |
| Model name | Always the alias above | Real API serves `Qwen/Qwen3-8B`. |

Everything else is passed through untouched: tool calls, JSON-schema output, streaming (SSE), and thinking text (`reasoning_content` or `<think>` blocks, depending on the backend).

Each request is logged as one line (status, tokens, latency). Prompts are not logged unless you pass `--log-bodies`.

## Caveats

- **Local is not the real model.** A 4-bit Llama 3.1 8B behaves differently from the hosted Qwen3-8B in answer quality, thinking-mode output and tool-call formatting. Use this for plumbing: clients, parsing, retries, the Streamlit flow, scorers. Do not use local outputs as evaluation baselines. Run the frozen eval set against the real API.
- Write parsing code that tolerates both `<think>` blocks and `reasoning_content`, and models that never think.
- The local server answers one request at a time anyway. Do not use it to estimate real throughput.
- Qwen weights are not permitted on the office laptop. Use `llama` there.
- The stand-in never needs or stores the real team API key. Do not paste the key into any `.env` that you use with it.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `cannot reach a backend` (exit 3) | Start LM Studio's server (Developer tab), or fix `--backend-url`. |
| `... is not loaded in the backend` (exit 4) | Load the model in LM Studio. Check the id with `curl localhost:1234/v1/models`. |
| 404 `model_not_found` from your code | `CHAT_MODEL` must equal the alias the banner prints. |
| 429 on every other call | Your code sends concurrent requests. Serialise it, add retry with `Retry-After`, or use `--queue`. |
| 400 `context_length_exceeded` | Shorten the prompt or lower `max_tokens`. The estimate is conservative. |
| 502 `backend_unreachable` mid-run | LM Studio stopped or unloaded the model. |

## Tests

```bash
pytest tools/mock_llm/tests
```
They use a fake in-process backend, so no model or LM Studio is needed.

## Files

| File | Purpose |
| --- | --- |
| `__main__.py` | CLI, model menu, startup checks |
| `models.py` | Model registry and backend-id matching |
| `limits.py` | Rate, concurrency and token-budget emulation |
| `proxy.py` | FastAPI proxy with streaming passthrough |
| `tests/` | Unit and proxy tests |

Design notes: `docs/decisions/mock_llm_design.md`.

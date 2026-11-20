"""Ask the agent one question and print the answer with its step trace. Run by a human.

    cd Code
    read -s API_KEY; export API_KEY            # key never goes in a file
    export CHAT_BASE_URL=https://103.42.50.41:1033/v1 CHAT_MODEL=Qwen/Qwen3-8B CAPSTONE_CA_CERT=...
    python scripts/ask.py "How many leave days do I have left?" --employee D010 --llm
    python scripts/ask.py --dev D01 --llm                  # a dev item: its question, profile, passages

Zscaler must be off. Policy search: the real retrieval once it exists, else the passages of
the --dev item, else nothing (policy questions then find no passages). --llm prints every model
call (tool_choice, tool calls returned, start of the text), which is how to see whether
Qwen3-8B calls tools sensibly. Never prints the key.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from hr_navigator.agents import orchestrator  # noqa: E402
from hr_navigator.agents.orchestrator import Agent  # noqa: E402
from hr_navigator.agents.toolbox import ToolBox  # noqa: E402
from hr_navigator.llm import config  # noqa: E402
from hr_navigator.schemas import AgentRequest, Passage  # noqa: E402
from hr_navigator.state.session_store import SessionStore  # noqa: E402

DEV = ROOT / "data/dev/dev_v1.jsonl"


def find_search(dev_item: dict | None):
    try:
        fn = getattr(importlib.import_module("hr_navigator.retrieval"), "search", None)
    except ImportError:
        fn = None
    if fn:
        return fn, "real retrieval"
    if dev_item:
        passages = [Passage(**p) for p in dev_item["passages"]]
        return (lambda query, k=5, filters=None: passages), "passages of the dev item"
    return (lambda query, k=5, filters=None: []), "NO retrieval (policy searches return nothing)"


def trace_llm(real_chat):
    def wrapper(messages, **kw):
        res = real_chat(messages, **kw)
        calls = [f"{c['function']['name']}({c['function']['arguments']})" for c in res.tool_calls]
        print(f"  [llm] {kw.get('tag')}: choice={kw.get('tool_choice')} tool_calls={calls or '-'} "
              f"finish={res.finish_reason} tokens={res.usage.tokens_in}+{res.usage.tokens_out}"
              f"{' (cached)' if res.cached else ''}\n        text: {res.text[:160]!r}", file=sys.stderr)
        return res
    return wrapper


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("question", nargs="?")
    ap.add_argument("--employee", help="HRMS employee id, for example D010")
    ap.add_argument("--dev", help="dev item id such as D01: use its question, profile and passages")
    ap.add_argument("--critic", choices=["off", "advisory", "revise"], default="advisory")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--session", default="", help="session id to keep memory across calls (SQLite)")
    ap.add_argument("--llm", action="store_true", help="print every model call")
    args = ap.parse_args(argv)

    item = None
    if args.dev:
        items = {json.loads(l)["qid"]: json.loads(l) for l in DEV.read_text().splitlines() if l.strip()}
        if args.dev not in items:
            raise SystemExit(f"no dev item {args.dev}; have {', '.join(items)}")
        item = items[args.dev]
    question = args.question or (item or {}).get("question")
    if not question:
        raise SystemExit("give a question, or --dev D01")

    try:
        endpoint = config.chat_endpoint()
    except RuntimeError as exc:
        raise SystemExit(f"config problem: {exc}")
    if urlparse(endpoint.base_url).hostname in ("127.0.0.1", "localhost"):
        print("WARNING: CHAT_BASE_URL is a local stand-in, not the course endpoint.", file=sys.stderr)

    search, source = find_search(item)
    print(f"model {endpoint.model} | search: {source} | critic: {args.critic}\n")
    if args.llm:
        orchestrator.chat = trace_llm(orchestrator.chat)
    agent = Agent(ToolBox(search), store=SessionStore() if args.session else None, critic=args.critic)
    profile = dict(item["employee_context"]) if item and not args.employee else None
    response, state = agent.run_with_state(
        AgentRequest(question=question, session_id=args.session, employee_id=args.employee), seed=args.seed,
        profile=profile)

    print("--- steps")
    for s in response.steps:
        print(" ", s["note"])
    print("--- tool calls")
    for t in response.tool_calls:
        print(f"  {t['name']}({json.dumps(t['args'], ensure_ascii=False)})" + (f"  ERROR {t['error']}" if t["error"] else ""))
    print(f"--- answer [{response.status}, behaviour {state.final_behaviour or '-'}]\n{response.answer}")
    for c in response.citations:
        print(f"  cite [{c.chunk_id}] {c.doc_id}: {c.quote[:120]!r}")
    print(f"--- tokens in {response.usage.tokens_in}, out {response.usage.tokens_out}, steps {state.steps}")
    return 0 if response.status != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())

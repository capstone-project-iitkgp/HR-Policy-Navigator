"""CLI entry point: ``python -m tools.mock_llm [--model qwen|llama] ...``."""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from typing import Callable, Sequence

import httpx

from .models import REGISTRY, ModelSpec, match_backend_model
from .proxy import Settings, create_app

DEFAULT_BACKEND = "http://localhost:1234/v1"  # LM Studio's default local server

EXIT_USAGE = 2
EXIT_BACKEND_DOWN = 3
EXIT_MODEL_MISSING = 4


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.mock_llm",
        description="Local stand-in for the shared course LLM endpoint.",
    )
    p.add_argument("--model", choices=sorted(REGISTRY), help="Which local model to serve. "
                   "Asked interactively if omitted in a terminal.")
    p.add_argument("--backend-url", default=DEFAULT_BACKEND,
                   help=f"OpenAI-compatible backend (default {DEFAULT_BACKEND}).")
    p.add_argument("--host", default="127.0.0.1", help="Listen address (default 127.0.0.1).")
    p.add_argument("--port", type=int, default=8099, help="Listen port (default 8099).")
    p.add_argument("--no-limits", action="store_true",
                   help="Disable rate/concurrency/token-limit emulation.")
    p.add_argument("--queue", action="store_true",
                   help="Queue concurrent requests instead of answering 429.")
    p.add_argument("--require-key", action="store_true",
                   help="Reject requests without an Authorization: Bearer header.")
    p.add_argument("--log-bodies", action="store_true",
                   help="Log request/response bodies (may contain sensitive text).")
    return p


def choose_model(
    arg: str | None,
    *,
    isatty: bool,
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> ModelSpec:
    """Return the chosen model, asking interactively when --model is omitted."""
    if arg:
        return REGISTRY[arg]
    if not isatty:
        print("error: --model is required when not running in a terminal "
              f"(choose from: {', '.join(sorted(REGISTRY))}).", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    keys = list(REGISTRY)
    out("Which model should the stand-in serve?")
    for i, key in enumerate(keys, 1):
        out(f"  {i}) {REGISTRY[key].label}")
    while True:
        answer = input_fn("Enter 1-%d: " % len(keys)).strip().lower()
        if answer in REGISTRY:
            return REGISTRY[answer]
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return REGISTRY[keys[int(answer) - 1]]
        out("Not a valid choice.")


def discover_backend_models(backend_url: str) -> list[str]:
    """List model ids the backend currently serves. Raises httpx.HTTPError."""
    r = httpx.get(backend_url.rstrip("/") + "/models", timeout=5.0)
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", [])]


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    spec = choose_model(args.model, isatty=sys.stdin.isatty())

    try:
        ids = discover_backend_models(args.backend_url)
    except (httpx.HTTPError, ValueError) as exc:
        print(f"error: cannot reach a backend at {args.backend_url} ({exc.__class__.__name__}).\n"
              "Start LM Studio's local server (Developer tab, or `lms server start`) or point "
              "--backend-url at Ollama (http://localhost:11434/v1) / llama-server.",
              file=sys.stderr)
        return EXIT_BACKEND_DOWN

    backend_id = match_backend_model(spec, ids)
    if backend_id is None:
        print(f"error: {spec.label} is not loaded in the backend.", file=sys.stderr)
        print("Models the backend reports: " + (", ".join(ids) or "(none)"), file=sys.stderr)
        if shutil.which("lms"):
            print("LM Studio CLI found. List downloaded models with `lms ls`, then load one with "
                  "`lms load <model_key> --context-length 8192`.", file=sys.stderr)
        if spec.note:
            print(spec.note, file=sys.stderr)
        return EXIT_MODEL_MISSING

    settings = Settings(
        backend_url=args.backend_url, limits=not args.no_limits, queue=args.queue,
        require_key=args.require_key, log_bodies=args.log_bodies,
    )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one log line per request is enough
    base = f"http://{args.host}:{args.port}/v1"
    print(
        f"\nmock_llm serving {spec.alias}  (backend model: {backend_id})\n"
        f"Limits: {'ON' if settings.limits else 'OFF'}"
        f"{', queueing' if settings.queue else ''}\n\n"
        "Put these in .env to use it:\n"
        f"  CHAT_BASE_URL={base}\n"
        f"  CHAT_MODEL={spec.alias}\n"
        "  API_KEY=dummy\n"
    )

    import uvicorn  # imported late so --help and tests stay light

    uvicorn.run(create_app(settings, spec, backend_id), host=args.host, port=args.port,
                log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

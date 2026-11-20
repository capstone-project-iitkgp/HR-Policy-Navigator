"""A tiny tracer with OpenTelemetry-shaped spans and OpenInference attribute names.

    tracer = get_tracer()
    with tracer.span("plan", "CHAIN", **{"input.value": question}) as sp:
        ...
        sp.set(**{"app.route": "policy"})

Spans nest through a ``contextvars`` variable, so no function signature carries a span. Finished
spans go to a sink (one JSON line each, ``.cache/traces/<date>.jsonl``). Tracing is OFF unless
``TRACE=1`` is set or ``configure(enabled=True)`` is called, and it never breaks the caller: an
error in the sink is logged once and ignored; an exception inside a span marks it ``error`` and
is re-raised unchanged.

Text is redacted when a span is written (``redact``): cut to 500 characters; e-mail addresses,
8+ digit numbers and key-like strings replaced. Employee ids are kept (pseudonyms).
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

log = logging.getLogger(__name__)

# OpenInference names (spec checked 20 Nov 2026 at arize-ai.github.io/openinference); ours use "app."
KIND = "openinference.span.kind"
INPUT, OUTPUT = "input.value", "output.value"
MODEL, FINISH = "llm.model_name", "llm.finish_reason"
TOK_IN, TOK_OUT, TOK_TOTAL = "llm.token_count.prompt", "llm.token_count.completion", "llm.token_count.total"
TOOL_NAME, TOOL_PARAMS = "tool.name", "tool.parameters"  # tool.parameters: UNVERIFIED against the spec
SESSION, USER = "session.id", "user.id"

MAX_TEXT = 500
DEFAULT_DIR = Path(__file__).resolve().parents[3] / ".cache" / "traces"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DIGITS = re.compile(r"\d{8,}")
_KEY = re.compile(r"\bsk-[A-Za-z0-9]{8,}|api[_ ]?key\s*[:=]\s*\S{6,}|\b[A-Za-z0-9_\-]{32,}\b", re.I)


def redact(value: Any) -> Any:
    """Redacted copy of an attribute value. Non-text scalars pass through; containers become JSON text."""
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = _KEY.sub("[key]", _EMAIL.sub("[email]", text))
    text = _DIGITS.sub("[number]", text)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "..."


class Sink(Protocol):
    def write(self, record: dict[str, Any]) -> None: ...


class JsonlSink:
    """Appends one JSON line per finished span to ``<directory>/<date>.jsonl``."""

    def __init__(self, directory: Path | str = DEFAULT_DIR):
        self.directory = Path(directory)
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        day = datetime.fromtimestamp(record["start_ns"] / 1e9).strftime("%Y-%m-%d")
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            with open(self.directory / f"{day}.jsonl", "a", encoding="utf-8") as f:
                f.write(line + "\n")


class Span:
    def __init__(self, name: str, kind: str, trace_id: str, span_id: str, parent_id: str | None,
                 start_ns: int, attributes: dict[str, Any]):
        self.name, self.trace_id, self.span_id, self.parent_id = name, trace_id, span_id, parent_id
        self.start_ns, self.end_ns, self.status = start_ns, 0, "ok"
        self.attributes: dict[str, Any] = {KIND: kind, **attributes}

    def set(self, **attrs: Any) -> None:
        """Add attributes. Names with dots go through ``**{"llm.model_name": ...}``."""
        self.attributes.update(attrs)

    def fail(self, message: str = "") -> None:
        """Mark the span as an error without raising (a tool that returned ok: false, a blocked call)."""
        self.status = "error"
        if message:
            self.attributes["error.message"] = message

    def record(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id, "span_id": self.span_id, "parent_id": self.parent_id, "name": self.name,
                "start_ns": self.start_ns, "end_ns": self.end_ns, "status": self.status,
                "attributes": {k: redact(v) for k, v in self.attributes.items()}}


class _NullSpan:
    """Stands in for a span when tracing is off: every call is accepted and ignored."""
    trace_id = span_id = ""

    def set(self, **attrs: Any) -> None: ...

    def fail(self, message: str = "") -> None: ...


_NULL = _NullSpan()
_current: contextvars.ContextVar[Span | None] = contextvars.ContextVar("hr_span", default=None)


def _hex(n_bytes: int) -> str:
    return os.urandom(n_bytes).hex()


class Tracer:
    def __init__(self, sink: Sink | None = None, *, enabled: bool = True, clock: Callable[[], int] = time.time_ns):
        self.sink, self.enabled, self._clock = sink, enabled, clock
        self._warned = False
        self.last_trace_id = ""  # trace id of the most recent root span (for --trace output)

    @contextmanager
    def span(self, name: str, kind: str = "CHAIN", **attrs: Any) -> Iterator[Span | _NullSpan]:
        if not self.enabled or self.sink is None:
            yield _NULL
            return
        parent = _current.get()
        sp = Span(name, kind, parent.trace_id if parent else _hex(16), _hex(8),
                  parent.span_id if parent else None, self._clock(), attrs)
        token = _current.set(sp)
        try:
            yield sp
        except BaseException as exc:
            sp.status = "error"
            sp.attributes["error.type"] = type(exc).__name__
            sp.attributes["error.message"] = str(exc)
            raise
        finally:
            _current.reset(token)
            sp.end_ns = self._clock()
            self._emit(sp)

    def _emit(self, sp: Span) -> None:
        if sp.parent_id is None:
            self.last_trace_id = sp.trace_id
        try:
            self.sink.write(sp.record())  # type: ignore[union-attr]
        except Exception:  # tracing must never break the agent
            if not self._warned:
                self._warned = True
                log.warning("trace sink failed; further failures are ignored", exc_info=True)


_tracer: Tracer | None = None


def configure(*, enabled: bool = True, directory: Path | str | None = None, sink: Sink | None = None) -> Tracer:
    """Set the process-wide tracer (``ask.py --trace``, tests). Returns it."""
    global _tracer
    _tracer = Tracer(sink or JsonlSink(directory or os.environ.get("TRACE_DIR") or DEFAULT_DIR), enabled=enabled)
    return _tracer


def get_tracer() -> Tracer:
    """The process-wide tracer: on if ``TRACE=1`` (or configure() was called), otherwise a no-op."""
    global _tracer
    if _tracer is None:
        _tracer = Tracer(JsonlSink(os.environ.get("TRACE_DIR") or DEFAULT_DIR),
                         enabled=os.environ.get("TRACE", "") not in ("", "0", "false"))
    return _tracer

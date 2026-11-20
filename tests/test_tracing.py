"""The tracer: nesting, timing, errors, redaction, and that it can never break the caller."""

import json

import pytest

from hr_navigator.observability import tracing
from hr_navigator.observability.tracing import JsonlSink, Tracer, redact


class ListSink:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


def ticking():
    t = iter(range(1000, 100000, 10))
    return lambda: next(t)


def test_spans_nest_and_share_a_trace_id_with_timings():
    sink = ListSink()
    tr = Tracer(sink, clock=ticking())
    with tr.span("root", "AGENT"):
        with tr.span("child", "LLM", **{"llm.model_name": "m"}):
            pass
        with tr.span("other", "TOOL"):
            pass
    child, other, root = sink.records  # children finish first
    assert root["parent_id"] is None and child["parent_id"] == other["parent_id"] == root["span_id"]
    assert len({r["trace_id"] for r in sink.records}) == 1
    assert root["start_ns"] < child["start_ns"] < child["end_ns"] < other["start_ns"] < root["end_ns"]
    assert child["attributes"]["openinference.span.kind"] == "LLM" and child["attributes"]["llm.model_name"] == "m"


def test_new_root_gets_a_new_trace():
    sink = ListSink()
    tr = Tracer(sink)
    for _ in range(2):
        with tr.span("r"):
            pass
    assert sink.records[0]["trace_id"] != sink.records[1]["trace_id"]


def test_exception_marks_the_span_and_is_reraised():
    sink = ListSink()
    with pytest.raises(ValueError):
        with Tracer(sink).span("boom") as sp:
            sp.set(x=1)
            raise ValueError("bad")
    r = sink.records[0]
    assert r["status"] == "error" and r["attributes"]["error.type"] == "ValueError" and r["attributes"]["x"] == 1


def test_fail_marks_error_without_raising():
    sink = ListSink()
    with Tracer(sink).span("tool") as sp:
        sp.fail("not_allowed")
    assert sink.records[0]["status"] == "error" and sink.records[0]["attributes"]["error.message"] == "not_allowed"


def test_disabled_tracer_writes_nothing_and_accepts_calls():
    sink = ListSink()
    with Tracer(sink, enabled=False).span("x") as sp:
        sp.set(a=1)
        sp.fail("ignored")
    assert sink.records == []


def test_a_failing_sink_never_breaks_the_caller(caplog):
    class Broken:
        def write(self, record):
            raise OSError("disk full")

    tr = Tracer(Broken())
    for _ in range(3):
        with tr.span("x"):
            result = 42
    assert result == 42 and sum("trace sink failed" in r.message for r in caplog.records) == 1


def test_redaction():
    assert redact("mail priya@corp.com now") == "mail [email] now"
    assert redact("account 123456789012") == "account [number]"
    assert redact("key sk-abcdefgh12345 and api_key: abcdef123456") == "key [key] and [key]"
    assert redact("A" * 40) == "[key]"
    long = "word " * 200
    assert redact(long).endswith("...") and len(redact(long)) == tracing.MAX_TEXT + 3
    assert redact(7) == 7 and redact({"a": "b@c.io"}) == '{"a": "[email]"}'
    assert redact("employee D010 asked for 5 days") == "employee D010 asked for 5 days"


def test_written_span_has_redacted_attributes():
    sink = ListSink()
    with Tracer(sink).span("q", **{"input.value": "my api key: secret123456 mail a@b.co"}):
        pass
    assert sink.records[0]["attributes"]["input.value"] == "my [key] mail [email]"


def test_jsonl_sink_appends_one_line_per_span(tmp_path):
    tr = Tracer(JsonlSink(tmp_path))
    with tr.span("a"):
        with tr.span("b"):
            pass
    (file,) = list(tmp_path.glob("*.jsonl"))
    rows = [json.loads(line) for line in file.read_text().splitlines()]
    assert [r["name"] for r in rows] == ["b", "a"]


def test_global_tracer_is_off_unless_asked(monkeypatch, tmp_path):
    monkeypatch.setattr(tracing, "_tracer", None)
    monkeypatch.delenv("TRACE", raising=False)
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    with tracing.get_tracer().span("x"):
        pass
    assert list(tmp_path.glob("*.jsonl")) == []
    monkeypatch.setattr(tracing, "_tracer", None)
    monkeypatch.setenv("TRACE", "1")
    with tracing.get_tracer().span("x"):
        pass
    assert len(list(tmp_path.glob("*.jsonl"))) == 1
    monkeypatch.setattr(tracing, "_tracer", None)  # leave no tracer behind for other tests

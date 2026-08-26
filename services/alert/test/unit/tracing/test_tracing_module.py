# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for ``src.tracing`` — the self-contained half of the OTel work.

These cover the parts that nine rounds of spec review could argue about but not
settle: the span lifecycle state machine, the redaction rules, and the
soft-dependency and off-by-default guarantees. Each maps to a TS id in the
feature test-spec.
"""

import os
import pathlib
import threading

import pytest

# The repo-root conftest puts `src/` on sys.path, so the service and its tests
# both import `tracing` (not `src.tracing`) — two spellings would be two module
# identities holding two copies of the pid-keyed provider state.
from opentelemetry import context as otel_context  # noqa: E402
from opentelemetry import trace as otel_trace  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

import tracing  # noqa: E402
from tracing import attributes as attrs_mod  # noqa: E402
from tracing import context as ctx_mod  # noqa: E402
from tracing import spans as spans_mod  # noqa: E402


@pytest.fixture
def exporter():
    """A tracer wired to an in-memory exporter, independent of global state."""
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    tracer = provider.get_tracer("test")
    yield exp, tracer
    provider.shutdown()


def _handle(tracer, **kw):
    """Build a handle bound to the test's tracer.

    Passing ``tracer`` matters: children are emitted from the handle's tracer, so
    a handle built without one sends them to the global provider and the test's
    exporter never sees them. ``open_root_span()`` always supplies it.
    """
    kw.setdefault("tracer", tracer)
    return spans_mod.RootSpanHandle(tracer.start_span(spans_mod.ROOT_SPAN_NAME), **kw)


# --------------------------------------------------------------------------
# Off by default / soft dependency  (REQ-016, REQ-019)
# --------------------------------------------------------------------------

def test_disabled_by_default(monkeypatch):
    """With the gate unset, init reports inactive and creates nothing."""
    monkeypatch.delenv("ENABLE_OTEL_MONITORING", raising=False)
    monkeypatch.setattr(tracing, "_initialised_pid", None)
    assert tracing.init_tracing() is False
    assert tracing.is_enabled() is False


def test_init_is_pid_keyed(monkeypatch):
    """A call from a different pid re-initialises rather than inheriting."""
    monkeypatch.delenv("ENABLE_OTEL_MONITORING", raising=False)
    monkeypatch.setattr(tracing, "_initialised_pid", os.getpid())
    monkeypatch.setattr(tracing, "_enabled", True)
    assert tracing.is_enabled() is True

    monkeypatch.setattr(tracing, "_initialised_pid", os.getpid() + 1)
    # Same module state, different pid: must not report this process as traced.
    assert tracing.is_enabled() is False


def test_shutdown_without_init_is_safe():
    tracing.shutdown()


# --------------------------------------------------------------------------
# Root span lifecycle  (REQ-001)
# --------------------------------------------------------------------------

def test_bypass_path_closes_decorated(exporter):
    """A path that never reached the recorder still exports a decorated span."""
    exp, tracer = exporter
    h = _handle(tracer)
    h.close(latency={}, message={"info": {"verdict": "confirmed"}},
            failure_reason="malformed_message")
    (span,) = exp.get_finished_spans()
    assert span.end_time is not None
    assert span.attributes["verdict"] == "confirmed"
    assert span.attributes["error_reason"] == "malformed_message"


def test_decorate_then_close_does_not_redecorate(exporter):
    exp, tracer = exporter
    h = _handle(tracer)
    h.decorate({}, {"info": {"verdict": "rejected"}})
    h.close()
    (span,) = exp.get_finished_spans()
    assert span.end_time is not None
    assert span.attributes["verdict"] == "rejected"


def test_close_is_idempotent_under_thread_race(exporter):
    """Eight closers race; exactly one span is exported and none errors."""
    exp, tracer = exporter
    h = _handle(tracer)
    errors = []

    def closer():
        try:
            h.close(latency={}, message={"info": {"verdict": "confirmed"}})
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=closer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(exp.get_finished_spans()) == 1


def test_open_root_span_never_raises(monkeypatch):
    """A failure inside open_root_span degrades to None, it does not propagate."""
    monkeypatch.setattr(spans_mod, "_otel", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert spans_mod.open_root_span({"sensorId": "s"}) is None


# --------------------------------------------------------------------------
# The close handshake — all four interleavings  (REQ-001)
# --------------------------------------------------------------------------

def test_inline_path_finally_closes(exporter):
    """Shipped default: no handoff, so the outer finally owns closure."""
    _, tracer = exporter
    h = _handle(tracer)
    h.mark_finalized()                        # inline _finalize ran
    assert h.should_close_from_callback() is False   # finally has not run yet
    h.mark_finally_reached()
    assert h.should_close_from_finally() is True


def test_deferred_resolving_after_finally(exporter):
    """Normal async: finally declines, the callback closes."""
    _, tracer = exporter
    h = _handle(tracer)
    assert h.mark_deferred() is True
    h.mark_finally_reached()
    assert h.should_close_from_finally() is False
    h.mark_finalized()
    assert h.should_close_from_callback() is True


def test_deferred_resolving_mid_try(exporter):
    """The window that is enrichment: callback declines, finally must still close.

    Without the ``or finalized`` clause both sides decline and the span leaks.
    """
    _, tracer = exporter
    h = _handle(tracer)
    assert h.mark_deferred() is True
    h.mark_finalized()                        # callback fired before the finally
    assert h.should_close_from_callback() is False
    h.mark_finally_reached()
    assert h.should_close_from_finally() is True


def test_already_resolved_future_refuses_handoff(exporter):
    """add_done_callback fired synchronously: the handoff must be refused.

    Recording it would leave the finally skipping a span nobody else closes.
    """
    _, tracer = exporter
    h = _handle(tracer)
    h.mark_finalized()                        # callback ran during registration
    assert h.mark_deferred() is False
    h.mark_finally_reached()
    assert h.should_close_from_finally() is True


# --------------------------------------------------------------------------
# Historical children  (REQ-002)
# --------------------------------------------------------------------------

def test_historical_children_from_timestamps(exporter):
    exp, tracer = exporter
    h = _handle(tracer)
    latency = {
        "timestamps": {
            "kafkaPublishedAt": "2026-08-24T00:00:00Z",
            "kafkaConsumedAt": "2026-08-24T00:00:01Z",
            "workerAssignedAt": "2026-08-24T00:00:02Z",
        },
        "getVideoStreamUrlWithOverlay": {"success": True, "duration": 1.5},
    }
    h.close(latency=latency, message={"info": {"verdict": "confirmed"}})
    names = [s.name for s in exp.get_finished_spans()]
    assert "Kafka Consume Lag" in names
    assert "Worker Queue Wait" in names
    assert "VST Video URL Resolution (overlay)" in names
    # event_loop-only stage absent when its keys are not stamped
    assert "Dispatch Wait" not in names


def test_snake_case_timestamps_accepted(exporter):
    exp, tracer = exporter
    h = _handle(tracer)
    h.close(latency={"timestamps": {
        "kafka_published_at": "2026-08-24T00:00:00Z",
        "kafka_consumed_at": "2026-08-24T00:00:01Z",
    }}, message={})
    assert "Kafka Consume Lag" in [s.name for s in exp.get_finished_spans()]


@pytest.mark.parametrize("bad", [None, "", "not-a-date",
                                 {"timestamps": {"kafkaPublishedAt": "x", "kafkaConsumedAt": "y"}}])
def test_malformed_latency_never_raises(exporter, bad):
    exp, tracer = exporter
    h = _handle(tracer)
    h.close(latency=bad if isinstance(bad, dict) else {"timestamps": {"kafkaPublishedAt": bad}},
            message={})
    assert len(exp.get_finished_spans()) >= 1      # root still closed


def test_negative_interval_is_skipped(exporter):
    """Stamps that disagree produce no span rather than a negative-duration one."""
    exp, tracer = exporter
    h = _handle(tracer)
    h.close(latency={"timestamps": {
        "kafkaPublishedAt": "2026-08-24T00:00:05Z",
        "kafkaConsumedAt": "2026-08-24T00:00:01Z",
    }}, message={})
    assert "Kafka Consume Lag" not in [s.name for s in exp.get_finished_spans()]


# --------------------------------------------------------------------------
# PII redaction  (REQ-014)
# --------------------------------------------------------------------------

def test_url_rules_per_attribute():
    """Four names, four transforms. url.query is blanked, not 'stripped'."""
    out = tracing.sanitize_attributes({
        "http.url": "https://vst/f.mp4?sig=SECRET&e=9",
        "url.full": "https://vst/f.mp4?sig=SECRET",
        "http.target": "/f.mp4?sig=SECRET",
        "url.query": "sig=SECRET&e=9",
    })
    assert out["http.url"] == "https://vst/f.mp4"
    assert out["url.full"] == "https://vst/f.mp4"
    assert out["http.target"] == "/f.mp4"
    assert out["url.query"] == ""
    assert "SECRET" not in " ".join(str(v) for v in out.values())


def test_exception_events_reduced_to_type_name():
    out = tracing.sanitize_attributes({
        "exception.type": "VSTError",
        "exception.message": "<html>body with PII</html>",
        "exception.stacktrace": "Traceback ... body with PII",
    })
    assert out["exception.message"] == "VSTError"
    assert out["exception.stacktrace"] == "VSTError"


def test_exception_without_type_falls_back():
    out = tracing.sanitize_attributes({"exception.message": "secret"})
    assert out["exception.message"] == "Exception"


def test_content_gate_open_keeps_exception_text():
    out = tracing.sanitize_attributes(
        {"exception.type": "VSTError", "exception.message": "detail"}, include_content=True)
    assert out["exception.message"] == "detail"


def test_manual_attributes_drop_content_by_default():
    out = attrs_mod.manual_attributes(
        {"sensorId": "cam-1", "category": "collision"},
        **{"vlm.prompt": "a prompt", "video.url": "https://vst/x?sig=S", "attempt": 2},
    )
    assert out["sensorId"] == "cam-1"
    assert out["attempt"] == 2
    assert "vlm.prompt" not in out
    assert "video.url" not in out


def test_manual_attributes_truncate_when_enabled():
    """max_content_chars is a maximum, suffix included.

    It used to be a floor: a full-length prefix plus a "...[+N chars]" marker put
    a 512 setting at 528. At a budget too small to hold any marker, a bare cut is
    what is left.
    """
    out = attrs_mod.manual_attributes(
        None, include_content=True, max_content_chars=64, **{"vlm.prompt": "x" * 500})
    assert out["vlm.prompt"].startswith("x")
    assert len(out["vlm.prompt"]) <= 64
    assert "chars]" in out["vlm.prompt"]

    tight = attrs_mod.manual_attributes(
        None, include_content=True, max_content_chars=10, **{"vlm.prompt": "x" * 50})
    assert tight["vlm.prompt"] == "x" * 10


def test_sanitizing_exporter_rebuilds_and_delegates():
    """The wrapper must reach every span, including auto-instrumented ones."""
    inner = InMemorySpanExporter()
    wrapper = tracing.SanitizingSpanExporter(inner)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(wrapper))
    tracer = provider.get_tracer("t")

    span = tracer.start_span("GET /x")
    span.set_attribute("http.url", "https://vst/f.mp4?sig=SECRET")
    span.end()
    provider.force_flush()

    (exported,) = inner.get_finished_spans()
    assert exported.attributes["http.url"] == "https://vst/f.mp4"
    provider.shutdown()


# --------------------------------------------------------------------------
# Context propagation  (REQ-007..REQ-009, REQ-012)
# --------------------------------------------------------------------------

_TP = "00-11111111111111111111111111111111-2222222222222222-01"


def test_kafka_headers_extract_bytes_and_str():
    for value in (_TP.encode(), _TP):
        ctx = ctx_mod.extract_context_from_kafka_headers([("traceparent", value)])
        assert ctx is not None
        sc = otel_trace.get_current_span(ctx).get_span_context()
        assert format(sc.trace_id, "032x") == "1" * 32


def test_kafka_headers_tolerate_junk():
    assert ctx_mod.extract_context_from_kafka_headers(None) is None
    assert ctx_mod.extract_context_from_kafka_headers([]) is None
    assert ctx_mod.extract_context_from_kafka_headers([("traceparent", b"\xff\xfe")]) is None
    assert ctx_mod.extract_context_from_kafka_headers([("traceparent", "garbage")]) is None
    assert ctx_mod.extract_context_from_kafka_headers([(None, b"x")]) is None


def test_kafka_duplicate_header_first_wins():
    ctx = ctx_mod.extract_context_from_kafka_headers(
        [("traceparent", _TP), ("traceparent", "garbage")])
    assert ctx is not None


def test_inject_is_noop_without_active_span():
    assert ctx_mod.inject_traceparent({}) == {}


def test_current_trace_ids_none_without_span():
    assert ctx_mod.current_trace_ids() == (None, None)


# --------------------------------------------------------------------------
# Backend client instrumentation  (REQ-009)
# --------------------------------------------------------------------------

def test_traced_io_wraps_sync_and_async(exporter):
    """The decorator must handle both, and must not change return values."""
    import asyncio

    _, tracer = exporter

    @spans_mod.traced_io("Elasticsearch write", **{"db.operation": "index"})
    def sync_call(x):
        return x * 2

    @spans_mod.traced_io("Elasticsearch get", **{"db.operation": "get"})
    async def async_call(x):
        return x + 1

    assert sync_call(21) == 42
    assert asyncio.run(async_call(1)) == 2


def test_traced_io_propagates_exceptions():
    """Tracing observes; it must never swallow the caller's failure."""

    @spans_mod.traced_io("Elasticsearch write")
    def boom():
        raise ValueError("backend down")

    with pytest.raises(ValueError, match="backend down"):
        boom()


def test_traced_io_preserves_metadata():
    @spans_mod.traced_io("Elasticsearch get")
    def documented():
        """Original docstring."""

    assert documented.__name__ == "documented"
    assert documented.__doc__ == "Original docstring."


def test_traced_io_never_leaks_call_arguments(monkeypatch):
    """The span carries the static attributes only — never the payload.

    An Elasticsearch document is exactly the shape of thing the "never log raw
    payloads" guideline exists to keep out of telemetry, so the decorator must
    not forward what it was called with.
    """
    captured = {}

    from contextlib import contextmanager

    @contextmanager
    def fake_live_span(name, **attributes):
        captured["name"] = name
        captured["attributes"] = attributes
        yield None

    monkeypatch.setattr(spans_mod, "live_span", fake_live_span)

    @spans_mod.traced_io("Elasticsearch write", **{"db.operation": "index"})
    def write(document, index=None):
        return "ok"

    assert write({"sensorId": "cam-1", "secret": "pii"}, index="mdx-vlm-alerts") == "ok"
    assert captured["name"] == "Elasticsearch write"
    assert captured["attributes"] == {"db.operation": "index"}
    flattened = repr(captured)
    assert "pii" not in flattened
    assert "mdx-vlm-alerts" not in flattened


# --------------------------------------------------------------------------
# Status description redaction  (REQ-014)
# --------------------------------------------------------------------------

def _status(description):
    from opentelemetry.trace import Status, StatusCode

    return Status(StatusCode.ERROR, description)


def test_status_description_reduced_to_exception_type():
    """The third redaction channel — the one the attribute rules do not reach.

    The SDK writes ``f"{type(exc).__name__}: {exc}"`` into the status whenever a
    span ends on an exception, so an Elasticsearch document built from an alert
    payload arrives here even when every attribute and event is already clean.
    """
    sanitized = tracing.sanitize_status(
        _status("ValueError: ES rejected doc: sensor cam-7 person John Doe at 10 Main St")
    )
    assert sanitized.description == "ValueError"


def test_status_description_drops_presigned_url_credentials():
    """The client instrumentors put transport errors here, URL and all."""
    leaked = (
        "ConnectionError: HTTPConnectionPool(host='vst', port=30888): Max retries "
        "exceeded with url: /vst/api?sig=SECRETSIG&token=TOK"
    )
    sanitized = tracing.sanitize_status(_status(leaked))
    assert sanitized.description == "ConnectionError"
    assert "SECRETSIG" not in sanitized.description
    assert "TOK" not in sanitized.description


def test_status_description_survives_the_content_gate():
    """Consistent with the attribute rules: an explicit opt-in keeps the detail."""
    kept = tracing.sanitize_status(_status("ValueError: raw detail"), include_content=True)
    assert kept.description == "ValueError: raw detail"


def test_status_sanitisation_tolerates_missing_and_empty():
    assert tracing.sanitize_status(None) is None
    empty = _status(None)
    assert tracing.sanitize_status(empty) is empty


def test_exported_span_carries_no_pii_in_any_channel(exporter):
    """End to end through the real exporter: attributes, events AND status."""
    sink = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        SimpleSpanProcessor(tracing.SanitizingSpanExporter(sink))
    )
    tracer = provider.get_tracer("test")

    with pytest.raises(ValueError):
        with tracer.start_as_current_span("Elasticsearch write") as span:
            span.set_attribute("http.url", "http://vst:30888/x?sig=SECRET")
            raise ValueError("sensor cam-7 person John Doe at 10 Main St")

    provider.force_flush()
    (exported,) = sink.get_finished_spans()
    haystack = repr(
        (dict(exported.attributes), [dict(e.attributes) for e in exported.events],
         exported.status.description)
    )
    assert "John Doe" not in haystack
    assert "SECRET" not in haystack
    provider.shutdown()


# --------------------------------------------------------------------------
# The disabled path really is disabled  (REQ-019, TS-064)
# --------------------------------------------------------------------------

@pytest.fixture
def tracing_state(monkeypatch):
    """Save and restore the module's pid-keyed state around a test.

    Also clears ENABLE_OTEL_MONITORING. Tests whose premise is "tracing is off"
    were otherwise at the mercy of how the suite was invoked, and passed alone
    while failing in a run with the feature enabled — which is exactly the run
    that matters, since the whole risk profile of this feature is what changes
    when it is switched on.
    """
    saved = (tracing._initialised_pid, tracing._enabled, tracing._provider)
    monkeypatch.delenv("ENABLE_OTEL_MONITORING", raising=False)
    yield
    tracing._initialised_pid, tracing._enabled, tracing._provider = saved


def test_no_span_is_created_when_tracing_is_disabled(tracing_state, monkeypatch):
    """REQ-019 verbatim: "no span is created (live or historical)".

    The guarantee is structural, not incidental: `requirements.txt` installs
    OpenTelemetry unconditionally, so "is the package importable" — which is all
    the earlier check asked — is always true in production and gated nothing.
    """
    monkeypatch.delenv("ENABLE_OTEL_MONITORING", raising=False)
    tracing._initialised_pid, tracing._enabled, tracing._provider = None, False, None

    assert spans_mod.open_root_span({"sensorId": "cam-1"}) is None
    with spans_mod.live_span("VLM Request", attempt=1) as span:
        assert span is None


def test_traced_io_creates_no_span_but_still_returns(tracing_state, monkeypatch):
    """The decorated call must be untouched — tracing off is not tracing broken."""
    monkeypatch.delenv("ENABLE_OTEL_MONITORING", raising=False)
    tracing._initialised_pid, tracing._enabled, tracing._provider = None, False, None

    @spans_mod.traced_io("Elasticsearch write")
    def write():
        return "ok"

    assert write() == "ok"


def test_disabled_path_attaches_no_context(tracing_state, monkeypatch):
    """A non-recording span still becoming *current* is the subtler half.

    Anything downstream that reads `get_current_span()` would see one, and every
    attach owes a detach.
    """
    monkeypatch.delenv("ENABLE_OTEL_MONITORING", raising=False)
    tracing._initialised_pid, tracing._enabled, tracing._provider = None, False, None

    before = otel_trace.get_current_span()
    handle = spans_mod.open_root_span({"sensorId": "cam-1"})
    assert handle is None
    assert otel_trace.get_current_span() is before


def _run_isolated(body: str, env: dict, tmp_path=None, sampling_ratio: float = 1.0) -> str:
    """Run a snippet in a fresh interpreter against a known config.

    Two reasons for the subprocess. Enabling tracing sets the process-global
    TracerProvider, which OpenTelemetry refuses to replace, so an in-process test
    would leak into every test after it. And the config is read once per process,
    so varying it means varying processes.

    ``sampling_ratio`` defaults to 1.0 because these tests assert that a span
    records. The shipped config samples at 0.1 — asserting ``is_recording()``
    against that would pass roughly one run in ten, which is a flaky test rather
    than a coverage gap.
    """
    import json
    import os
    import subprocess
    import sys
    import textwrap

    src = pathlib.Path(__file__).resolve().parents[3] / "src"
    extra_env = dict(env)
    if tmp_path is not None:
        config = tmp_path / "config.yaml"
        config.write_text(json.dumps(  # JSON is valid YAML
            {"alert_agent": {"tracing": {"sampling_ratio": sampling_ratio}}}
        ))
        extra_env["CONFIG_PATH"] = str(config)

    script = f"import sys; sys.path.insert(0, {str(src)!r})\n" + textwrap.dedent(body)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=60,
        # "none" is a default, not an override: a caller testing the exporter
        # wiring has to be able to ask for a real one.
        env={**os.environ, "OTEL_TRACES_EXPORTER": "none", **extra_env},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_the_pipeline_process_initialises_itself(tracing_state, tmp_path):
    """CRITICAL: nothing calls init_tracing() in the pipeline process.

    REQ-020 puts that call in `enhance_alert_with_vlm.py`, which the multi-core
    work rewrites, so it is not editable here yet. Until it is, the root-span
    opener initialises its own process — otherwise switching the feature on gives
    an operator REST spans and a silent pipeline, which reads as "the pipeline
    produced nothing" rather than "the pipeline was never instrumented".
    """
    out = _run_isolated(
        """
        import tracing, tracing.spans as S
        before = tracing.is_enabled()
        handle = S.open_root_span({"sensorId": "cam-1"}, pipeline_mode="event_loop")
        print(before, tracing.is_enabled(), handle is not None, handle._span.is_recording())
        """,
        {"ENABLE_OTEL_MONITORING": "true"},
        tmp_path,
    )
    assert out.split() == ["False", "True", "True", "True"]


def test_a_forked_worker_builds_its_own_provider(tracing_state, tmp_path):
    """The reason initialisation sits at the span site rather than in `__main__`.

    State is keyed on pid. A child that inherited an initialised parent's module
    state would read a stale pid, conclude tracing is not enabled for it, and
    emit nothing — and the multi-core work makes forked pipeline workers the
    normal case rather than an edge one.
    """
    out = _run_isolated(
        """
        import os, tracing, tracing.spans as S
        msg = {"sensorId": "cam-1"}
        S.open_root_span(msg, pipeline_mode="event_loop").detach()
        r, w = os.pipe()
        if os.fork() == 0:
            os.close(r)
            h = S.open_root_span(msg, pipeline_mode="event_loop")
            os.write(w, f"{tracing.is_enabled()} {h is not None} {h._span.is_recording()}".encode())
            os.close(w); os._exit(0)
        os.close(w); os.wait()
        print(os.read(r, 100).decode())
        """,
        {"ENABLE_OTEL_MONITORING": "true"},
        tmp_path,
    )
    assert out.split() == ["True", "True", "True"]


def test_live_span_keeps_initial_attributes_when_the_body_raises(tracing_state, tmp_path):
    """The mechanism REQ-003's `success=false` depends on.

    Runs in a subprocess: it needs the global TracerProvider, which OpenTelemetry
    refuses to replace once one is set — and with the feature enabled one always
    is, so an in-process version passed alone and failed in the run that matters.
    """
    out = _run_isolated(
        """
        from opentelemetry import trace
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        import tracing, tracing.spans as S

        tracing.init_tracing()
        sink = InMemorySpanExporter()
        trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(sink))
        try:
            with S.live_span("VLM Request", attempt=1, max_retries=1, success=False):
                raise TimeoutError("vlm timed out")
        except TimeoutError:
            pass
        trace.get_tracer_provider().force_flush()
        attrs = dict(sink.get_finished_spans()[0].attributes)
        print(attrs.get("success"), attrs.get("exception.type"))
        """,
        {"ENABLE_OTEL_MONITORING": "true"}, tmp_path,
    )
    success, exc_type = out.split()
    assert success == "False", "the attribute set at creation did not survive the raise"
    assert exc_type == "TimeoutError"

def test_sampling_ratio_comes_from_config(tracing_state, tmp_path):
    """REQ-015: "No profile ships an always-100% default"."""
    out = _run_isolated(
        """
        import tracing
        from opentelemetry import trace
        tracing.init_tracing()
        sampler = trace.get_tracer_provider().sampler
        print(getattr(sampler._root, "rate", None) or getattr(sampler._root, "_rate", None))
        """,
        {"ENABLE_OTEL_MONITORING": "true"}, tmp_path, sampling_ratio=0.25,
    )
    assert out == "0.25"


# Every config a deployment actually mounts. `services/alert/config.yaml` is
# baked into the image and reached only by the Dockerfile's default CMD, which
# compose overrides — the alert-bridge container reads /app/runtime/config.yml,
# rendered from the bind-mounted profile file. An earlier version of this test
# asserted against config.yaml alone and passed while every deployment resolved
# sampling to the code default.
_REPO = pathlib.Path(__file__).resolve().parents[5]
DEPLOYED_CONFIGS = [
    _REPO / "deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/config.yml",
    _REPO / "deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/EDGE-LOCAL-VLM-config.yml",
    _REPO / "deploy/docker/industry-profiles/warehouse-operations/vlm-as-verifier/configs/config.yml",
    _REPO / "deploy/docker/industry-profiles/smartcities/vlm-as-verifier/configs/config.yml",
    _REPO / "services/alert/blueprint_config/config_warehouse_blueprint.yaml",
    _REPO / "services/alert/blueprint_config/config_smartcity_blueprint.yaml",
    _REPO / "services/alert/blueprint_config/config_public_safety_blueprint.yaml",
    _REPO / "services/alert/config.yaml",
]
# Helm templates. They carry Go templating so they cannot be parsed as YAML;
# checked textually instead, which still catches the block going missing.
HELM_CONFIGS = [
    _REPO / "deploy/helm/services/alert/configs/config.yml",
    _REPO / "deploy/helm/services/alert/configs/EDGE-LOCAL-VLM-config.yml",
]


@pytest.mark.parametrize("config_path", DEPLOYED_CONFIGS, ids=lambda p: p.name)
def test_every_deployed_config_ships_the_tracing_block(config_path):
    """REQ-015: "No profile ships an always-100% default"."""
    import yaml

    assert config_path.exists(), f"{config_path} moved; this test is now blind"
    block = (yaml.safe_load(config_path.read_text())["alert_agent"] or {}).get("tracing")
    assert block is not None, "alert_agent.tracing is missing; sampling falls back to the code default"
    # Pinned to the shipped value, like the Helm half of this audit asserts
    # textually. A range check let a config drift to 0.99 -- effectively the
    # always-on sampling REQ-015 forbids -- while still passing.
    assert block["sampling_ratio"] == 0.1
    assert block["include_content"] is False


@pytest.mark.parametrize("config_path", HELM_CONFIGS, ids=lambda p: p.name)
def test_helm_configs_ship_the_tracing_block(config_path):
    import re

    text = config_path.read_text()
    body = re.search(r"^alert_agent:$(.*?)(?=^\S)", text, re.M | re.S)
    assert body, "alert_agent block not found"
    assert "\n  tracing:\n" in body.group(1)
    assert "\n    sampling_ratio: 0.1\n" in body.group(1)
    assert "\n    include_content: false\n" in body.group(1)


def test_no_shipped_config_escapes_the_tracing_audit():
    """The two lists above must name every shipped config, not most of them.

    They were hand-written and missed both industry profiles — the product
    profiles, the ones a customer actually deploys. Nothing failed, because
    ``DEFAULT_SAMPLING_RATIO`` happens to equal what the other eight declare;
    the audit was simply blind to them. Discover the set instead of trusting
    the list, so profile number eleven cannot arrive unnoticed.

    Bounded on purpose: it finds a *top-level* ``alert_agent:``. A config that
    nested the block under a parent key would still escape, which is the same
    class of miss — but matching at any indentation also matches the heredoc
    inside the CI workflow and every test fixture, so the cure is worse. If a
    nested variant is ever shipped, this needs a real YAML walk.
    """
    import re
    import subprocess

    audited = {p.resolve() for p in DEPLOYED_CONFIGS + HELM_CONFIGS}

    # Tracked files only. Walking the filesystem meant a customer config copied
    # into the checkout to reproduce a bug reddened the suite, which is an
    # ordinary thing to do and nothing to do with this code.
    try:
        listing = subprocess.run(
            ["git", "-C", str(_REPO), "ls-files", "-z", "*.yml", "*.yaml"],
            capture_output=True, text=True,
        )
    except OSError:  # no git binary at all
        pytest.skip("git is not available; the audit cannot enumerate tracked configs")
    if listing.returncode != 0:
        pytest.skip("not a git checkout; the audit cannot enumerate tracked configs")

    found = set()
    for rel in listing.stdout.split("\0"):
        if not rel or "test" in pathlib.PurePosixPath(rel).parts:
            continue
        path = _REPO / rel
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        # \s*$ rather than $: a CRLF checkout leaves "alert_agent:\r" and the
        # bare anchor would miss every config on it.
        if re.search(r"^alert_agent:\s*$", text, re.M):
            found.add(path.resolve())

    assert found, "discovery found nothing; the walk is broken, not the configs"
    assert found == audited, (
        "shipped configs carrying alert_agent: are not the ones audited above.\n"
        f"  missing from the lists: {sorted(str(p.relative_to(_REPO)) for p in found - audited)}\n"
        f"  listed but not found:   {sorted(str(p.relative_to(_REPO)) for p in audited - found)}"
    )


def test_the_code_default_matches_what_the_configs_ship(tracing_state):
    """The fallback must not invert the intent.

    It was 1.0 while the configs said 0.1, so any deployment missing the block —
    which was all of them — got the opposite of what was shipped.
    """
    assert tracing.DEFAULT_SAMPLING_RATIO == 0.1
    assert tracing._resolve({}, "sampling_ratio", None,
                            tracing.DEFAULT_SAMPLING_RATIO, tracing._as_ratio) == 0.1


@pytest.mark.parametrize(
    "value,expected",
    [(False, False), ("false", False), ("no", False), ("off", False), (0, False),
     ("", False), ("maybe", False), (None, False),
     (True, True), ("true", True), ("yes", True), (1, True)],
)
def test_content_gate_fails_closed(value, expected):
    """`bool("false")` is True, and that was the cast this gate used.

    One pair of quotes in a hand-maintained profile YAML turned prompt text,
    VLM responses and presigned video URLs back on. A gate that fails open is
    worse than no gate.
    """
    config = {} if value is None else {"include_content": value}
    assert tracing._resolve(config, "include_content", None, False, tracing._as_bool) is expected


def test_out_of_range_sampling_is_clamped_not_fatal(tracing_state):
    """`sampling_ratio: 50` (meaning percent) used to disable tracing entirely.

    TraceIdRatioBased raises, init_tracing's broad except swallows it, and the
    operator gets "tracing initialisation failed" pointing at the SDK rather than
    at their own value.
    """
    resolve = lambda v: tracing._resolve({"sampling_ratio": v}, "sampling_ratio", None, 0.1, tracing._as_ratio)
    assert resolve(50) == 1.0
    assert resolve(-1) == 0.0
    assert resolve(0.25) == 0.25
    assert resolve("abc") == 0.1


def test_negative_content_budget_is_rejected_not_unbounded():
    """A negative max_chars disabled truncation, which with include_content on
    would emit whole prompts and VLM responses."""
    assert tracing._resolve({"max_content_chars": -1}, "max_content_chars", None, 512,
                            tracing._as_chars) == 512


def test_content_gate_reaches_live_spans(tracing_state, tmp_path):
    """The hole the gated builder exists to close.

    `live_span` used to pass its kwargs straight to `start_span`, so CONTENT_KEYS
    was never consulted on that path and a caller passing a prompt would have
    emitted it. The exporter backstop does not cover this — it knows the four URL
    names and the two exception names, not the content keys.
    """
    out = _run_isolated(
        """
        import tracing, tracing.spans as S
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        from opentelemetry import trace
        tracing.init_tracing()
        sink = InMemorySpanExporter()
        trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(sink))
        with S.live_span("VLM Request", **{"vlm.prompt": "SECRET PROMPT", "attempt": 1}):
            pass
        attrs = dict(sink.get_finished_spans()[0].attributes)
        print("vlm.prompt" in attrs, attrs.get("attempt"))
        """,
        {"ENABLE_OTEL_MONITORING": "true"}, tmp_path,
    )
    assert out.split() == ["False", "1"], "a content key reached a live span"


# --------------------------------------------------------------------------
# Soft dependency  (REQ-016, TS-048/TS-049)
# --------------------------------------------------------------------------

def test_everything_degrades_when_opentelemetry_is_absent(tmp_path):
    """The real thing: `opentelemetry` genuinely unimportable, not patched.

    A `sys.modules` stub is not this test — the package is already imported by
    the time a test runs, so stubbing proves only that the stub works. Blocking
    it at the finder is what reproduces a deployment that installed
    requirements.txt without the optional extras.

    Two `# pragma: no cover` comments in this package cite "the soft-dependency
    test". Until now there was no such test.
    """
    import subprocess
    import sys
    import textwrap

    src = pathlib.Path(__file__).resolve().parents[3] / "src"
    script = textwrap.dedent(
        f'''
        import sys

        class Blocker:
            def find_module(self, name, path=None):
                return self if name.split(".")[0] == "opentelemetry" else None
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] == "opentelemetry":
                    raise ImportError("blocked")
                return None

        for mod in [m for m in sys.modules if m.split(".")[0] == "opentelemetry"]:
            del sys.modules[mod]
        sys.meta_path.insert(0, Blocker())
        sys.path.insert(0, {str(src)!r})

        import tracing, tracing.spans as S, tracing.context as C
        results = [
            tracing.init_tracing() is False,
            tracing.is_enabled() is False,
            tracing.ensure_initialised() is False,
            tracing.instrument_fastapi_app(object()) is False,
            S.open_root_span({{"sensorId": "cam-1"}}) is None,
            C.current_trace_ids() == (None, None),
            C.inject_traceparent({{}}) == {{}},
            C.extract_context_from_kafka_headers([("traceparent", b"x")]) is None,
        ]
        with S.live_span("VLM Request") as span:
            results.append(span is None)

        @S.traced_io("Elasticsearch write")
        def write():
            return "ok"
        results.append(write() == "ok")

        handle = S.RootSpanHandle(None)
        handle.mark_finally_reached()
        handle.close(None, None)
        handle.detach()
        results.append(True)

        tracing.shutdown()
        print("opentelemetry" not in sys.modules, all(results), results.count(False))
        '''
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    absent, all_ok, failures = result.stdout.split()
    assert absent == "True", "opentelemetry was importable; the test proved nothing"
    assert all_ok == "True", f"{failures} entry points did not degrade cleanly"


def test_the_logging_filter_survives_a_missing_tracing_package(monkeypatch):
    """Correlation is an aid — it must not be able to cost the service its logs."""
    import builtins
    import logging

    from utils.logging_config import _TraceContextFilter

    real_import = builtins.__import__

    def explode(name, *args, **kwargs):
        if name == "tracing":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", explode)
    record = logging.LogRecord("ab", logging.INFO, __file__, 1, "msg", (), None)
    assert _TraceContextFilter().filter(record) is True
    assert record.trace_id == ""


def test_concurrent_first_alerts_all_get_a_span(tracing_state, tmp_path):
    """Initialisation happens on the hot path, so it happens concurrently.

    `ensure_initialised()` is reached from `open_root_span`, which means a
    process's first few alerts can arrive together and race into it. Publishing
    the pid on entry rather than on completion advertised "initialised" while the
    provider was still being built, and a caller arriving in that window took the
    fast path and was told tracing was off — silently untraced alerts, on exactly
    the first alerts a process handles.

    The provider build is slowed here so the window is wide enough to observe;
    without that the race exists but almost never lands.
    """
    out = _run_isolated(
        """
        import threading, time
        import opentelemetry.sdk.trace as sdk

        real = sdk.TracerProvider
        built = []

        class Slow(real):
            def __init__(self, *a, **k):
                built.append(1)
                time.sleep(0.30)
                super().__init__(*a, **k)

        sdk.TracerProvider = Slow
        import tracing.spans as S

        got = {}
        def first():
            got["first"] = S.open_root_span({"sensorId": "cam-1"}) is not None
        def during():
            time.sleep(0.10)
            got["during"] = S.open_root_span({"sensorId": "cam-1"}) is not None

        a, b = threading.Thread(target=first), threading.Thread(target=during)
        a.start(); b.start(); a.join(); b.join()
        print(got["first"], got["during"], len(built))
        """,
        {"ENABLE_OTEL_MONITORING": "true"}, tmp_path,
    )
    first, during, providers = out.split()
    assert first == "True"
    assert during == "True", "an alert arriving during initialisation was untraced"
    assert providers == "1", "the racing callers each built a provider"


def test_malformed_epochs_produce_no_historical_child():
    """A pre-epoch child renders as starting decades before its parent.

    Worse than a missing one: an absent span reads as absent, a 1969 span reads
    as a broken trace. `bool` is included because it is an `int` subclass, so
    `True` would otherwise be accepted as 1970-01-01T00:00:01.
    """
    for raw in (-1, -1e9, float("nan"), True, False, "1969-12-31T23:59:59Z"):
        assert spans_mod._epoch_seconds(raw) is None, f"{raw!r} was accepted"

    assert spans_mod._epoch_seconds(0) == 0.0
    assert spans_mod._epoch_seconds("2026-08-25T00:00:00Z") > 0


# --------------------------------------------------------------------------
# Sampled-out events do no work  (REQ-019, TS-064)
# --------------------------------------------------------------------------

def _unsampled_root():
    """A root whose sampler said no, on a provider that is otherwise real."""
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF

    provider = TracerProvider(sampler=ALWAYS_OFF)
    tracer = provider.get_tracer("test")
    span = tracer.start_span("Alert Verification")
    assert span.is_recording() is False
    return provider, tracer, span


def test_historical_children_are_not_built_for_an_unsampled_root():
    """TS-064. Sampling decides at start_span, so without this every child on a
    dropped trace is constructed and thrown away — 23us/event measured, on the
    90% of events a 0.1 ratio drops.

    Counting `start_span` calls rather than exported spans: an unsampled child
    exports nothing either way, so only the call count distinguishes "did no
    work" from "did the work and discarded it".
    """
    provider, tracer, span = _unsampled_root()
    calls = []
    real = tracer.start_span
    tracer.start_span = lambda *a, **k: (calls.append(a[0] if a else None), real(*a, **k))[1]

    spans_mod.build_historical_children(
        span,
        {"timestamps": {"kafkaPublishedAt": 1787000000.0, "kafkaConsumedAt": 1787000000.1,
                        "workerAssignedAt": 1787000000.2, "taskDispatchedAt": 1787000000.3,
                        "taskStartedAt": 1787000000.4}},
        tracer,
    )
    assert calls == [], f"built {len(calls)} child spans for a sampled-out root"
    provider.shutdown()


def test_decorate_does_no_work_on_an_unsampled_root():
    provider, tracer, span = _unsampled_root()
    handle = spans_mod.RootSpanHandle(span, tracer=tracer)
    assert handle.is_recording() is False

    calls = []
    real = tracer.start_span
    tracer.start_span = lambda *a, **k: (calls.append(a[0] if a else None), real(*a, **k))[1]
    attrs = []
    span.set_attribute = lambda k, v: attrs.append(k)

    handle.decorate(
        {"timestamps": {"kafkaPublishedAt": 1787000000.0, "kafkaConsumedAt": 1787000000.1}},
        {"verification_result": "true"},
        failure_reason="boom",
    )
    assert calls == [], f"built {len(calls)} child spans for a sampled-out root"
    assert attrs == [], f"set {attrs} on a sampled-out root"
    # The flag is still claimed — decoration has had its turn either way, and a
    # second caller must not retry it.
    assert handle._decorated is True
    provider.shutdown()


def test_root_handle_reports_recording_state():
    """`RootSpanHandle.is_recording()` is what the guards above key off."""
    provider, tracer, span = _unsampled_root()
    assert spans_mod.RootSpanHandle(span, tracer=tracer).is_recording() is False

    sampled = TracerProvider()
    live = sampled.get_tracer("t").start_span("Alert Verification")
    assert spans_mod.RootSpanHandle(live, tracer=sampled.get_tracer("t")).is_recording() is True
    assert spans_mod.RootSpanHandle(None).is_recording() is False
    provider.shutdown()
    sampled.shutdown()


# --------------------------------------------------------------------------
# The root covers the stages it contains  (REQ-002)
# --------------------------------------------------------------------------

def _iso(delta_seconds):
    import datetime as dt

    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delta_seconds)).isoformat()


def test_every_historical_child_sits_inside_the_root(tracing_state, tmp_path):
    """Three of the five stages finish before this coroutine ever runs.

    Kafka Consume Lag, Worker Queue Wait and Dispatch Wait all describe time
    spent before the pipeline picked the event up, so a root started at function
    entry is shorter than the stages it contains, and Jaeger draws those children
    to the left of their own parent. The root's bar is what an operator reads as
    "how long did this alert take".

    Run in a subprocess: it needs the global TracerProvider, which OpenTelemetry
    refuses to replace once another test has set one.
    """
    out = _run_isolated(
        """
        import datetime as dt
        from opentelemetry import trace
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        import tracing, tracing.spans as S

        tracing.init_tracing()
        sink = InMemorySpanExporter()
        trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(sink))

        def iso(d):
            return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=d)).isoformat()

        ts = {"kafkaPublishedAt": iso(-2.5), "kafkaConsumedAt": iso(-2.0),
              "workerAssignedAt": iso(-1.5), "taskDispatchedAt": iso(-0.6),
              "taskStartedAt": iso(0)}
        h = S.open_root_span({"sensorId": "cam-1"}, pipeline_mode="event_loop", timestamps=ts)
        h.decorate({"timestamps": ts}, {"verification_result": "true"})
        h.close(None, None); h.detach()
        trace.get_tracer_provider().force_flush()

        spans = sink.get_finished_spans()
        root = next(s for s in spans if s.name == S.ROOT_SPAN_NAME)
        children = [s for s in spans if s is not root]
        outside = [s.name for s in children
                   if not (root.start_time <= s.start_time and s.end_time <= root.end_time)]
        print(len(children), len(outside), round((root.end_time - root.start_time) / 1e9, 1))
        """,
        {"ENABLE_OTEL_MONITORING": "true"}, tmp_path,
    )
    children, outside, duration = out.split()
    assert int(children) >= 3, "no historical children were built"
    assert outside == "0", f"{outside} children fell outside their own parent"
    assert float(duration) >= 2.4, "the root does not span the stages it contains"


def test_earliest_stamp_picks_pipeline_order_and_refuses_the_future():
    """First valid stamp in pipeline order, not the numeric minimum.

    Order is the semantic answer to "when did this event arrive", and one
    corrupted stamp should not drag the root's start backwards. A stamp ahead of
    this host's clock is refused outright — it would start the root after its own
    children.
    """
    published, consumed = _iso(-5), _iso(-4)
    assert spans_mod.earliest_stamp(
        {"kafkaPublishedAt": published, "kafkaConsumedAt": consumed}
    ) == pytest.approx(spans_mod._epoch_seconds(published))

    # Missing leading stamp: fall through to the next one.
    assert spans_mod.earliest_stamp(
        {"kafkaPublishedAt": None, "kafkaConsumedAt": consumed}
    ) == pytest.approx(spans_mod._epoch_seconds(consumed))

    assert spans_mod.earliest_stamp({"kafkaPublishedAt": _iso(3600)}) is None
    assert spans_mod.earliest_stamp({}) is None
    assert spans_mod.earliest_stamp(None) is None


# --------------------------------------------------------------------------
# The redaction wiring, not just the redaction  (REQ-014, TS-045)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("exporter_name", ["otlp", "console"])
def test_init_tracing_installs_the_sanitizing_exporter(tracing_state, tmp_path, exporter_name):
    """The one line that makes redaction reach auto-instrumented spans.

    Every other test builds SanitizingSpanExporter by hand, so deleting the wrap
    from _build_exporter left the suite green — the mechanism was correct and its
    installation was exactly as unguarded as the leak it replaced. Parametrised
    over the exporter so the "wraps whichever exporter is configured, not OTLP
    specifically" property is pinned too.
    """
    out = _run_isolated(
        """
        import tracing
        from opentelemetry import trace
        tracing.init_tracing()
        processors = trace.get_tracer_provider()._active_span_processor._span_processors
        exporters = [type(getattr(p, "span_exporter", None)).__name__ for p in processors]
        print(",".join(exporters))
        """,
        {"ENABLE_OTEL_MONITORING": "true", "OTEL_TRACES_EXPORTER": exporter_name}, tmp_path,
    )
    assert "SanitizingSpanExporter" in out, (
        f"provider exports through {out} — spans reach the backend unredacted"
    )


def test_init_tracing_deletes_request_header_capture(tracing_state, tmp_path):
    """TS-045. Arbitrary request headers must not reach spans (REQ-014).

    The instrumentation's own documentation offers `X-.*` as the worked example,
    which is exactly what this forbids — so a deployment setting it must not win.
    """
    out = _run_isolated(
        """
        import os, tracing
        tracing.init_tracing()
        print(
            "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST" in os.environ,
            "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST" in os.environ,
            os.environ.get("OTEL_SEMCONV_STABILITY_OPT_IN"),
        )
        """,
        {
            "ENABLE_OTEL_MONITORING": "true",
            "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST": "X-.*",
            "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST": "X-.*",
        },
        tmp_path,
    )
    server, client, semconv = out.split()
    assert server == "False", "server request-header capture survived initialisation"
    assert client == "False", "client request-header capture survived initialisation"
    # REQ-005(a): pinned so the caller user-agent attribute keeps a stable name.
    assert semconv == "http/dup"


def test_root_span_name_is_the_documented_one():
    """Jaeger queries and any dashboard key off this exact string (REQ-002)."""
    assert spans_mod.ROOT_SPAN_NAME == "Alert Verification"


def test_elasticsearch_io_methods_are_traced():
    """`functools.wraps` leaves `__wrapped__`, so decoration is checkable."""
    from clients.elastic import ElasticClient

    expected = {
        "write_json", "write_json_async", "update_document",
        "get_document", "get_document_async",
    }
    undecorated = [
        name for name in sorted(expected)
        if not hasattr(getattr(ElasticClient, name, None), "__wrapped__")
    ]
    assert not undecorated, f"Elasticsearch methods lost their span: {undecorated}"


def test_status_with_no_colon_fails_closed():
    """"Reduce to the type name" has no answer for an unrecognised shape.

    Splitting unconditionally returned the whole string, which is the opposite of
    what this function is for. Not reachable from the pinned packages — every
    description they write is "Type: message" — but the contract should not
    depend on that staying true.
    """
    sanitized = tracing.sanitize_status(_status("connection refused to internal-host"))
    assert sanitized.description == ""


def test_close_and_decorate_are_idempotent(exporter):
    """One span, one end — the invariant the whole handle exists for."""
    _, tracer = exporter
    handle = _handle(tracer)
    handle.decorate({"timestamps": {}}, {"verification_result": "true"})
    assert handle._decorated is True
    handle.decorate({"timestamps": {}}, {"verification_result": "false"})

    ends = []
    handle._span.end = lambda *a, **k: ends.append(1)
    handle.mark_finally_reached()
    handle.close(None, {"verification_result": "true"})
    handle.close(None, {"verification_result": "true"})
    assert len(ends) == 1, f"span ended {len(ends)} times"


def test_the_module_docstring_describes_what_ships():
    """The docstring has been wrong in both directions now.

    It first claimed two closers existed when there was one; corrected, it then
    claimed the handoff had no production caller — which stayed on the page for
    exactly one revision after the caller landed. Either way a reader reasons
    about machinery that does not match the code.
    """
    doc = spans_mod.__doc__
    assert "Two closers, both wired." in doc
    assert "Exactly two closers exist" not in doc
    assert "no production caller yet" not in doc


def test_the_handoff_methods_have_production_callers():
    """The inverse of the check this replaces.

    That one greped `src/` only, so it kept passing after the callers landed in
    `enhance_alert_with_vlm.py`, which sits above `src/`. It was asserting
    something false and reporting green.
    """
    import subprocess

    root = pathlib.Path(__file__).resolve().parents[3]
    for name in ("mark_deferred", "mark_finalized", "should_close_from_callback"):
        hits = subprocess.run(
            ["grep", "-rn", "--include=*.py", name, str(root / "src"), str(root / "enhance_alert_with_vlm.py")],
            capture_output=True, text=True,
        ).stdout.splitlines()
        callers = [h for h in hits if "/tracing/spans.py" not in h]
        assert callers, (
            f"{name} has no production caller — if that is now true, the module "
            "docstring must say so again"
        )

def test_log_lines_inside_a_span_carry_the_trace_ids(tracing_state, tmp_path):
    """REQ-012's positive half. The three existing tests are all negative —
    byte-identity, formatter identity, idempotence — so deleting the append
    entirely left the suite green. REG-009 depends on logstash seeing this exact
    suffix."""
    out = _run_isolated(
        """
        import io, logging, re
        from opentelemetry import trace
        import tracing
        from utils.logging_config import _install_trace_correlation

        tracing.init_tracing()
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
        logging.root.addHandler(handler)
        logging.root.setLevel(logging.INFO)
        _install_trace_correlation()

        with trace.get_tracer("t").start_as_current_span("Alert Verification"):
            logging.getLogger("ab").info("alert processed")
        line = buf.getvalue()
        print(bool(re.search(r" trace_id=[0-9a-f]{32} span_id=[0-9a-f]{16}\\n$", line)))
        """,
        {"ENABLE_OTEL_MONITORING": "true"}, tmp_path,
    )
    assert out == "True", "a line logged inside a span carried no trace ids"


def test_root_span_forwards_the_configured_content_policy(tracing_state, monkeypatch, exporter):
    """The config knob has to reach the root span's attribute builder.

    `open_root_span` took `include_content` as a parameter defaulting to False and
    the pipeline passes nothing, so `alert_agent.tracing.include_content` was read
    at init and then discarded here.

    Asserting on what is forwarded rather than on an emitted attribute, because
    the root carries no CONTENT_KEYS today — the plumbing is what is under test,
    and it is what a future content attribute on this span will depend on.
    """
    import os

    tracing._content_policy = (True, 64)
    tracing._initialised_pid, tracing._enabled = os.getpid(), True

    seen = {}
    real = spans_mod.manual_attributes

    def spy(message, **kwargs):
        seen.update(kwargs)
        return real(message, **kwargs)

    monkeypatch.setattr(spans_mod, "manual_attributes", spy)
    handle = spans_mod.open_root_span({"sensorId": "cam-1"}, pipeline_mode="event_loop")
    if handle is not None:
        handle.detach()

    assert seen.get("include_content") is True, "the configured content gate did not reach the root"
    assert seen.get("max_content_chars") == 64


def test_detach_restores_the_previous_context(exporter):
    """Every attach owes a detach.

    `open_root_span` makes the root current, and the pipeline's `finally` hands
    the token back. Skipping that leaks the span into whatever the worker handles
    next, so the following alert's spans parent themselves under the previous
    alert's root.
    """
    _, tracer = exporter
    before = otel_trace.get_current_span()

    span = tracer.start_span("Alert Verification")
    token = otel_context.attach(otel_trace.set_span_in_context(span))
    handle = spans_mod.RootSpanHandle(span, context_token=token, tracer=tracer)
    assert otel_trace.get_current_span() is span

    handle.detach()
    assert otel_trace.get_current_span() is before, "the root stayed current after detach"


# --------------------------------------------------------------------------
# Tracing observes; it never rewrites the caller's control flow  (REQ-019)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sampled", [True, False], ids=["sampled", "unsampled"])
def test_the_callers_exception_survives_every_manual_span(tracing_state, sampled):
    """Both branches, because the defect lived on exactly one of them.

    `live_span` is a @contextmanager, and contextlib re-throws the body's
    exception AT the suspended yield. A `yield` placed inside a `try` that
    catches therefore swallows the caller's error and yields a second time, and
    the caller gets `RuntimeError: generator didn't stop after throw()` instead.

    That is what the unsampled-parent guard did when it first shipped. At the
    0.1 ratio the configs carry it rewrote the exception on ~90% of events, which
    made the pipeline's `except (APITimeoutError, APIConnectionError, ...)`
    unreachable: VLM transport failures stopped being retried and were published
    with response code 500 instead of 504. The suite was green throughout,
    because it was only ever run with the feature off.
    """
    import asyncio
    import os

    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON

    provider = TracerProvider(sampler=ALWAYS_ON if sampled else ALWAYS_OFF)
    tracing._initialised_pid, tracing._enabled = os.getpid(), True

    with provider.get_tracer("t").start_as_current_span("Alert Verification") as root:
        assert root.is_recording() is sampled

        with pytest.raises(TimeoutError, match="vlm timed out"):
            with spans_mod.live_span("VLM Request", attempt=1):
                raise TimeoutError("vlm timed out")

        @spans_mod.traced_io("Elasticsearch write")
        def write():
            raise ValueError("es down")

        with pytest.raises(ValueError, match="es down"):
            write()

        @spans_mod.traced_io("Elasticsearch write")
        async def write_async():
            raise ValueError("es down")

        with pytest.raises(ValueError, match="es down"):
            asyncio.run(write_async())

    provider.shutdown()


def test_every_swallowing_handler_in_live_span_is_yield_free():
    """Structural guard on the shape, not just the symptom.

    contextlib re-throws the caller's exception AT the suspended yield, so a
    yield inside a `try` whose handler *swallows* means the caller's error is
    swallowed too. A handler that re-raises is fine, and `live_span` has one on
    purpose — it records `exception.type` and re-raises.

    The behavioural test above catches this, but only for the exception types it
    happens to raise. This pins the property that made it possible.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(spans_mod.live_span)))

    def reraises(handler):
        return any(
            isinstance(n, ast.Raise) and n.exc is None for n in ast.walk(handler)
        )

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not node.handlers:
            continue
        if all(reraises(h) for h in node.handlers):
            continue
        for stmt in node.body:
            offenders += [
                sub.lineno for sub in ast.walk(stmt) if isinstance(sub, ast.Yield)
            ]

    assert not offenders, (
        f"yield at line(s) {offenders} of live_span sits inside a try whose handler "
        "swallows — the caller's exception would be replaced"
    )


# --------------------------------------------------------------------------
# Configuration robustness  (REQ-013, REQ-019)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value", [2, -1, 0.5, float("nan"), float("inf"), "maybe", {"a": 1}, [1]],
)
def test_content_gate_fails_closed_on_every_odd_scalar(value):
    """The numeric branch used to read any non-zero as True — including NaN.

    `include_content: 2` is the same one-character typo class as the quoted
    'false' this coercion was written for, and NaN failed closed for sampling
    while failing open for PII.
    """
    assert tracing._resolve(
        {"include_content": value}, "include_content", None, False, tracing._as_bool
    ) is False


@pytest.mark.parametrize("value", ["false", "off", "no", 0, 2, float("nan")])
def test_explicit_overrides_go_through_the_same_cast(value):
    """`init_tracing(include_content="false")` re-created the PII bug one call
    path over: the override branch returned its argument raw.

    This is an exported, documented entry point, and the obvious future caller is
    the eager initialisation at the pipeline process entry.
    """
    assert tracing._resolve(
        {}, "include_content", value, False, tracing._as_bool
    ) is False


@pytest.mark.parametrize(
    "block",
    [
        {"max_content_chars": float("inf")},
        {"sampling_ratio": "abc"},
        {"include_content": {"nested": "dict"}},
        {"sampling_ratio": None, "include_content": None},
    ],
)
def test_init_tracing_never_raises_on_a_pathological_config(tracing_state, tmp_path, block):
    """"Never raises" is the contract, and web/main.py calls this at module scope.

    Config parsing sits before the SDK try/except, so `max_content_chars: .inf`
    raised OverflowError out of init_tracing and the API process failed to
    import — tracing did not degrade, the service stopped starting.
    """
    import json
    import subprocess
    import sys
    import textwrap

    config = tmp_path / "config.yaml"
    config.write_text(json.dumps({"alert_agent": {"tracing": block}}))
    src = pathlib.Path(__file__).resolve().parents[3] / "src"
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys; sys.path.insert(0, {str(src)!r})
            import tracing
            print(tracing.init_tracing())
        """)],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "ENABLE_OTEL_MONITORING": "true",
             "OTEL_TRACES_EXPORTER": "none", "CONFIG_PATH": str(config)},
    )
    assert result.returncode == 0, f"init_tracing raised:\n{result.stderr}"


def test_init_tracing_installs_the_http_client_instrumentors(tracing_state, tmp_path):
    """REQ-009's only production wiring.

    Deleting the `_instrument_http_clients()` call left the suite green — the
    same defect class as the sanitizer wiring, which was filed and fixed a round
    earlier. Outbound client spans and the `traceparent` VST and the VLM need to
    join the trace both hang off this one line.
    """
    out = _run_isolated(
        """
        import tracing
        tracing.init_tracing()
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        print(RequestsInstrumentor().is_instrumented_by_opentelemetry,
              HTTPXClientInstrumentor().is_instrumented_by_opentelemetry)
        """,
        {"ENABLE_OTEL_MONITORING": "true"}, tmp_path,
    )
    requests_on, httpx_on = out.split()
    assert requests_on == "True", "requests is not instrumented; VST spans and traceparent are absent"
    assert httpx_on == "True", "httpx is not instrumented; VLM spans and traceparent are absent"


def test_open_root_span_makes_the_root_current(tracing_state, tmp_path):
    """SG2-001 by name: with no span ever made current, REQ-009, REQ-003 and
    REQ-012 all specify behaviour that silently cannot work.

    The existing tests build the attach by hand or use `start_as_current_span`
    directly, so replacing `open_root_span`'s attach with `token = None` left
    them all green.
    """
    out = _run_isolated(
        """
        from opentelemetry import trace
        import tracing, tracing.spans as S
        tracing.init_tracing()
        h = S.open_root_span({"sensorId": "cam-1"}, pipeline_mode="event_loop")
        during = trace.get_current_span() is h._span
        ids_during = tracing.current_trace_ids()[0] is not None
        h.detach()
        after = tracing.current_trace_ids()[0] is None
        print(during, ids_during, after)
        """,
        {"ENABLE_OTEL_MONITORING": "true"}, tmp_path,
    )
    during, ids, after = out.split()
    assert during == "True", "open_root_span did not make the root current"
    assert ids == "True", "log correlation sees no trace inside the root"
    assert after == "True", "the context was not restored on detach"


def test_earliest_stamp_accepts_the_snake_case_spelling():
    """The sync path stamps snake_case, and the children already honour it.

    Reading the dict directly meant a snake_case event got no start_time, so its
    three pre-entry children rendered left of their own parent — the exact defect
    the start_time fix exists to remove, for the spelling `_SNAKE` exists to
    serve.
    """
    published = _iso(-5)
    assert spans_mod.earliest_stamp({"kafka_published_at": published}) == pytest.approx(
        spans_mod._epoch_seconds(published)
    )


def test_the_sanitizer_fails_closed_when_rebuilding_raises(monkeypatch):
    """REQ-014's last line of defence: never export unsanitised.

    Dropping the FAILURE return exported the raw batch instead, and nothing
    noticed.
    """
    from opentelemetry.sdk.trace.export import SpanExportResult

    sink = InMemorySpanExporter()
    wrapper = tracing.SanitizingSpanExporter(sink)
    monkeypatch.setattr(
        wrapper, "_rebuild", lambda span: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    provider = TracerProvider()
    span = provider.get_tracer("t").start_span("x")
    span.end()

    assert wrapper.export([span]) is SpanExportResult.FAILURE
    assert sink.get_finished_spans() == (), "an unsanitised batch reached the exporter"
    provider.shutdown()


def test_close_ends_the_span_even_with_no_message(exporter):
    """`close(None, None)` must still end the root.

    Every other test reaches close() with a message, which runs decorate() first.
    The uncovered path is the one where nothing decorates — and a span that never
    ends is never exported, orphaning every child under it.
    """
    exp, tracer = exporter
    handle = _handle(tracer)
    handle.mark_finally_reached()
    handle.close(None, None)
    assert [s.name for s in exp.get_finished_spans()] == [spans_mod.ROOT_SPAN_NAME]


def test_error_reason_is_absent_on_the_success_path(exporter):
    """`if failure_reason:` guards against recording the string "None".

    Made unconditional, every successful alert carries error_reason="None" and
    any "show me failed alerts" query in Jaeger or Phoenix stops discriminating.
    """
    exp, tracer = exporter
    handle = _handle(tracer)
    handle.decorate(None, {"verification_result": "true"}, failure_reason=None)
    handle.mark_finally_reached()
    handle.close(None, None)
    attributes = exp.get_finished_spans()[0].attributes
    assert "error_reason" not in attributes


def test_decorate_builds_the_children_exactly_once(exporter):
    """Idempotence matters as soon as the deferred recorder hook lands — that is
    the whole reason the guard exists."""
    exp, tracer = exporter
    handle = _handle(tracer)
    latency = {"timestamps": {"kafkaPublishedAt": 1787000000.0,
                              "kafkaConsumedAt": 1787000000.1}}
    handle.decorate(latency, {"verification_result": "true"})
    handle.decorate(latency, {"verification_result": "true"})
    names = [s.name for s in exp.get_finished_spans()]
    assert names.count("Kafka Consume Lag") == 1, f"children built {names.count('Kafka Consume Lag')} times"


@pytest.mark.parametrize(
    "value,expected",
    [(float("inf"), 0.1), (float("-inf"), 0.1), (float("nan"), 0.1),
     (50, 1.0), (-1, 0.0), (0.25, 0.25)],
)
def test_non_finite_sampling_falls_back_rather_than_clamping(value, expected):
    """`.inf` clamped up gives 100% sampling — fail-open on volume.

    Same shape as reading a non-boolean as True, one field over: a range clamp is
    right for 50 (someone meant percent) and wrong for infinity, which is not a
    range problem. REQ-015 forbids a 100% default however it is arrived at.
    """
    assert tracing._resolve(
        {"sampling_ratio": value}, "sampling_ratio", None, 0.1, tracing._as_ratio
    ) == expected


def test_zero_content_budget_is_rejected():
    """0 is neither "no limit" nor a usable limit.

    Every content attribute becomes an empty string, which `_put` drops, so the
    gate reads as open while nothing is emitted.
    """
    assert tracing._resolve(
        {"max_content_chars": 0}, "max_content_chars", None, 512, tracing._as_chars
    ) == 512


def test_close_records_the_failure_reason_without_a_message(exporter):
    """`message` is None on the deferred and on-demand paths.

    Guarding decoration on it dropped both the failure reason and the historical
    children — the feature's main deliverable — on exactly the paths that arrive
    with the multi-core work.
    """
    exp, tracer = exporter
    handle = _handle(tracer)
    handle.mark_finally_reached()
    handle.close(
        {"timestamps": {"kafkaPublishedAt": 1787000000.0, "kafkaConsumedAt": 1787000000.1}},
        None,
        failure_reason="boom",
    )
    spans = exp.get_finished_spans()
    root = next(s for s in spans if s.name == spans_mod.ROOT_SPAN_NAME)
    assert root.attributes.get("error_reason") == "boom"
    assert any(s.name == "Kafka Consume Lag" for s in spans)


def test_content_policy_does_not_survive_into_a_fresh_process(tracing_state, tmp_path):
    """A forked child that does not enable tracing must not inherit the policy.

    Tracing is deliberately OFF here: on the success path the policy is
    overwritten anyway, so the reset only matters when initialisation returns
    early — which is the case a fork with the feature off actually takes.
    """
    out = _run_isolated(
        """
        import tracing
        tracing._content_policy = (True, 4096)      # as if inherited from a parent
        tracing._initialised_pid = None
        print(tracing.init_tracing(), tracing.content_policy())
        """,
        {"ENABLE_OTEL_MONITORING": "false"}, tmp_path,
    )
    assert out == "False (False, 512)", f"stale policy survived: {out}"


# --------------------------------------------------------------------------
# The recorder decorates, above the Prometheus gate  (REQ-001)
# --------------------------------------------------------------------------

def test_record_event_complete_decorates_with_prometheus_off(exporter):
    """`record_event_complete` is the only site that knows *why* an event ended.

    The span is closed elsewhere, so decorating here is the only route by which
    `malformed_message` or `no_prompt` reaches the trace — and below the
    Prometheus gate it would be lost on any deployment running with metrics off,
    which is the shipped default. That default is what this drives; the ordering
    against the gate is pinned structurally below, because monkeypatching the
    flag on does not make the metric objects importable.
    """
    from metrics import recorder

    exp, tracer = exporter
    handle = _handle(tracer)
    recorder.record_event_complete(
        worker_start_time=0.0,
        message={"sensorId": "cam-1"},
        latency={"timestamps": {}},
        failure_reason="no_prompt",
        span_handle=handle,
    )
    handle.mark_finally_reached()
    handle.close(None, None)

    root = next(s for s in exp.get_finished_spans() if s.name == spans_mod.ROOT_SPAN_NAME)
    assert root.attributes.get("error_reason") == "no_prompt"


def test_the_decorate_hook_sits_above_the_prometheus_gate():
    """Below it, the failure reason is lost whenever metrics are off."""
    import ast
    import inspect
    import textwrap

    from metrics import recorder

    tree = ast.parse(textwrap.dedent(inspect.getsource(recorder.record_event_complete)))
    fn = tree.body[0]
    decorate_at = next(
        stmt.lineno for stmt in fn.body
        for n in ast.walk(stmt)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "decorate"
    )
    gate_at = next(
        stmt.lineno for stmt in fn.body
        if isinstance(stmt, ast.If) and "PROMETHEUS_ENABLED" in ast.dump(stmt.test)
    )
    assert decorate_at < gate_at, "the decorate hook is below the Prometheus gate"


def test_record_event_complete_never_closes_the_span(exporter):
    """Closing here would land before post-publish enrichment, truncating the
    root and orphaning whatever enrichment creates."""
    from metrics import recorder

    exp, tracer = exporter
    handle = _handle(tracer)
    recorder.record_event_complete(
        worker_start_time=0.0, message={"sensorId": "cam-1"},
        latency={"timestamps": {}}, failure_reason="malformed_message",
        span_handle=handle,
    )
    assert exp.get_finished_spans() == (), "the recorder ended the span"
    assert handle._closed is False


# --------------------------------------------------------------------------
# The deferred-sink handoff, behaviourally  (REQ-001 concurrency contract)
# --------------------------------------------------------------------------

def test_deferred_handoff_yields_exactly_one_span(exporter):
    """The `finally` runs first and defers; the sink callback closes.

    This is the two-thread handoff the whole lock exists for. Neither side may
    close twice, and neither may decline and leave the span open forever.
    """
    exp, tracer = exporter
    handle = _handle(tracer)

    # Pipeline thread: the finally reaches its decision after deferring.
    assert handle.mark_deferred() is True
    handle.mark_finally_reached()
    assert handle.should_close_from_finally() is False, "the finally must not close a deferred span"

    # Sink thread, later.
    handle.mark_finalized()
    assert handle.should_close_from_callback() is True
    handle.close(None, {"verification_result": "true"})
    handle.close(None, {"verification_result": "true"})   # a second callback must be inert

    assert len(exp.get_finished_spans()) == 1


def test_a_callback_that_already_ran_refuses_the_handoff(exporter):
    """`add_done_callback` on a resolved future fires synchronously.

    If the callback has already run, `mark_deferred()` must refuse — otherwise
    the finally would defer to something that has already declined, and nobody
    closes the span.
    """
    exp, tracer = exporter
    handle = _handle(tracer)

    handle.mark_finalized()                     # the callback ran first
    assert handle.mark_deferred() is False, "deferring to a callback that already ran"

    handle.mark_finally_reached()
    assert handle.should_close_from_finally() is True
    handle.close(None, {"verification_result": "true"})
    assert len(exp.get_finished_spans()) == 1


# --------------------------------------------------------------------------
# On-demand verification gets its own linked root  (REQ-006)
# --------------------------------------------------------------------------

def test_a_background_task_root_links_rather_than_parents(tracing_state, tmp_path):
    """Starlette runs background tasks after the response is sent.

    By then the FastAPI server span has ended, so a parent/child edge would put
    a child outside its parent's lifetime — the span-lifetime violation REQ-006
    exists to avoid. A Link carries the association without the ordering claim.
    """
    out = _run_isolated(
        """
        from opentelemetry import trace
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        import tracing, tracing.spans as S

        tracing.init_tracing()
        sink = InMemorySpanExporter()
        trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(sink))

        # The request: its span ends before the task runs.
        with trace.get_tracer("t").start_as_current_span("POST /verification/ondemand"):
            captured = tracing.current_span_context()
        request_trace = format(captured.trace_id, "032x")

        h = S.open_root_span({"sensorId": "cam-1"}, pipeline_mode="ondemand", link_to=captured)
        h.close(None, None); h.detach()
        trace.get_tracer_provider().force_flush()

        root = [s for s in sink.get_finished_spans() if s.name == S.ROOT_SPAN_NAME][0]
        linked = [format(l.context.trace_id, "032x") for l in (root.links or [])]
        print(root.parent is None, request_trace in linked,
              format(root.context.trace_id, "032x") != request_trace)
        """,
        {"ENABLE_OTEL_MONITORING": "true"}, tmp_path,
    )
    no_parent, links_back, own_trace = out.split()
    assert no_parent == "True", "the background root was parented to a span that had ended"
    assert links_back == "True", "the root carries no link back to its request"
    assert own_trace == "True", "the root did not start its own trace"


def test_current_span_context_is_none_outside_a_span(tracing_state):
    """The capture site must degrade rather than invent a context."""
    assert tracing.current_span_context() is None


# --------------------------------------------------------------------------
# Outbound Kafka trace context  (REQ-007, sending half)
# --------------------------------------------------------------------------

def test_kafka_headers_are_none_when_untraced(tracing_state):
    """`None`, not `[]`: confluent-kafka treats None as "no headers", which is
    byte-for-byte the produce this service already makes."""
    assert ctx_mod.kafka_headers_for_current_span() is None


def test_kafka_headers_carry_the_current_traceparent(tracing_state, tmp_path):
    out = _run_isolated(
        """
        from opentelemetry import trace
        import tracing
        from tracing import context as C
        tracing.init_tracing()
        with trace.get_tracer("t").start_as_current_span("Alert Verification") as s:
            headers = C.kafka_headers_for_current_span()
            expected = format(s.get_span_context().trace_id, "032x")
        names = [k for k, _ in headers]
        value = dict(headers)[b"traceparent"] if b"traceparent" in dict(headers) else dict(headers)["traceparent"]
        print("traceparent" in names, expected in value.decode(), isinstance(value, bytes))
        """,
        {"ENABLE_OTEL_MONITORING": "true"}, tmp_path,
    )
    present, carries_trace, is_bytes = out.split()
    assert present == "True"
    assert carries_trace == "True", "the header does not carry the current trace"
    assert is_bytes == "True", "confluent-kafka wants bytes header values"


# --------------------------------------------------------------------------
# Additive OTel metrics  (REQ-011)
# --------------------------------------------------------------------------

def test_metric_recording_is_silent_when_uninitialised(tracing_state):
    """A metrics fault must cost a data point, never an alert."""
    from tracing import meters

    meters.observe_verification_duration(1.0, pipeline_mode="event_loop", verdict="true")
    meters.count_vlm_attempt(success=False, attempt=2)
    meters.observe_capacity_wait(seconds=0.5, service="vst")
    meters.shutdown()


def test_metrics_are_otlp_only_and_never_touch_the_prometheus_registry(tracing_state, tmp_path):
    """A PrometheusMetricReader would be invisible and look wired.

    `metrics/__init__.py` auto-sets PROMETHEUS_MULTIPROC_DIR, so :9081 serves a
    MultiProcessCollector registry built from on-disk shards; an in-process OTel
    reader registers into the default registry, which that endpoint never reads.
    """
    out = _run_isolated(
        """
        import tracing
        from tracing import meters
        from opentelemetry import metrics as otel_metrics
        tracing.init_tracing()
        readers = otel_metrics.get_meter_provider()._all_metric_readers
        kinds = sorted(type(r).__name__ for r in readers)
        print(len(readers), ",".join(kinds), len(meters._instruments))
        """,
        # Explicit, because OTEL_METRICS_EXPORTER falls back to
        # OTEL_TRACES_EXPORTER, and _run_isolated defaults that to "none" —
        # which is the behaviour the next test asserts.
        {"ENABLE_OTEL_MONITORING": "true", "OTEL_METRICS_EXPORTER": "otlp"}, tmp_path,
    )
    count, kinds, instruments = out.split()
    assert kinds == "PeriodicExportingMetricReader", f"unexpected reader(s): {kinds}"
    assert "Prometheus" not in kinds
    assert instruments == "3", "the three REQ-011 instruments are not all created"


def test_metrics_record_with_prometheus_off(tracing_state, tmp_path):
    """TS-067: acceptance is recording, not registration.

    The previous version of this test parsed each function and asserted the OTel
    call sat above that function's own Prometheus gate. `observe_pipeline_latency`
    passed it while being unreachable — its only production caller sits *below*
    the gate in `record_event_complete`, so clearing a function's own guard
    proved nothing. Reachability is what matters, and only driving the code
    shows it.
    """
    out = _run_isolated(
        """
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from opentelemetry import metrics as otel_metrics
        import tracing
        from tracing import meters

        reader = InMemoryMetricReader()
        otel_metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
        meters._initialised_pid = None
        tracing.init_tracing()
        # Rebuild the instruments against the in-memory provider.
        meter = otel_metrics.get_meter_provider().get_meter("test")
        meters._instruments = {
            "verification_duration": meter.create_histogram("alert.verification.duration"),
            "vlm_attempts": meter.create_counter("alert.vlm.attempt.count"),
            "capacity_wait": meter.create_histogram("alert.capacity.wait.duration"),
        }

        from metrics import recorder
        assert recorder.PROMETHEUS_ENABLED is False, "this test is about the metrics-off default"
        recorder.record_event_complete(
            worker_start_time=0.0,
            message={"sensorId": "cam-1", "end": "2020-01-01T00:00:00+00:00",
                     "info": {"verdict": "true"}},
            latency={"timestamps": {}},
            pipeline_mode="event_loop",
        )
        recorder.observe_capacity_wait("vst", 0.25)
        meters.count_vlm_attempt(success=False, attempt=1)

        names, dims = set(), set()
        for rm in (reader.get_metrics_data().resource_metrics or []):
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    names.add(m.name)
                    for pt in m.data.data_points:
                        dims |= set(pt.attributes or {})
        print(len(names), ",".join(sorted(names)), "pipeline_mode" in dims)
        """,
        {"ENABLE_OTEL_MONITORING": "true"}, tmp_path,
    )
    count, names, has_mode = out.split()
    assert int(count) == 3, f"only {count} of the three instruments recorded: {names}"
    assert has_mode == "True", "pipeline_mode never reaches the verification-duration points"



def test_the_deferred_handoff_completes_even_if_the_recorder_raises(exporter):
    """`concurrent.futures` swallows a callback's exception.

    So if the recorder raised inside the sink callback, the callback would never
    mark itself finalized and never close — while the pipeline's `finally` has
    already declined, having deferred. The span would never end, never export,
    and orphan every child under it, which is the one thing this module exists
    to prevent.
    """
    exp, tracer = exporter
    handle = _handle(tracer)

    # The pipeline thread defers and stands down.
    assert handle.mark_deferred() is True
    handle.mark_finally_reached()
    assert handle.should_close_from_finally() is False

    # The sink callback, with a recorder that blows up. Mirrors
    # `_complete_event_after_publish._finalize`'s try/finally exactly.
    def finalize():
        try:
            raise RuntimeError("recorder exploded")
        finally:
            handle.mark_finalized()
            if handle.should_close_from_callback():
                handle.close(None, {"verification_result": "true"})

    with pytest.raises(RuntimeError, match="recorder exploded"):
        finalize()

    assert len(exp.get_finished_spans()) == 1, "the span was stranded by a recorder failure"


def test_metrics_follow_the_traces_exporter_switch(tracing_state, tmp_path):
    """`OTEL_TRACES_EXPORTER=none` must not leave metrics hammering a collector.

    The meter provider is built by hand, so the SDK's auto-configuration never
    reads OTEL_METRICS_EXPORTER for it. Without this, the obvious operator lever
    silenced traces while metrics kept retrying OTLP — three backoffs and roughly
    seven seconds blocked at process exit, against docker stop's ten-second
    grace for a whole pipeline fleet.
    """
    out = _run_isolated(
        """
        import tracing
        from tracing import meters
        tracing.init_tracing()
        print(meters._provider is None, len(meters._instruments))
        """,
        {"ENABLE_OTEL_MONITORING": "true", "OTEL_TRACES_EXPORTER": "none"}, tmp_path,
    )
    disabled, instruments = out.split()
    assert disabled == "True", "metrics still export when traces are switched off"
    assert instruments == "0"


def test_metrics_exporter_can_be_set_independently(tracing_state, tmp_path):
    out = _run_isolated(
        """
        import tracing
        from tracing import meters
        tracing.init_tracing()
        print(meters._provider is not None)
        """,
        {"ENABLE_OTEL_MONITORING": "true", "OTEL_TRACES_EXPORTER": "none",
         "OTEL_METRICS_EXPORTER": "console"}, tmp_path,
    )
    assert out == "True", "an explicit OTEL_METRICS_EXPORTER did not win"


def test_log_correlation_does_no_sdk_work_when_tracing_is_off(tracing_state, monkeypatch):
    """The filter runs on every log record, installed unconditionally.

    It has to be: correlation must survive the two `setup_logging` fallback
    branches. But `tracing/__init__.py` promises "no latency added" with the
    feature off, and reaching into the SDK per record cost +55%.

    Asserted as "the propagator is never consulted" rather than as a wall-clock
    threshold — a timing assertion in a unit suite is a flake waiting for a busy
    machine, and the property is what actually matters.
    """
    reached = []
    monkeypatch.setattr(ctx_mod, "_propagators", lambda: reached.append(1))
    # Cleared explicitly. The fixture restores state afterwards but does not
    # reset it first, so a prior test that enabled tracing for this pid leaves
    # this one's premise false — and only in the run with the feature on, which
    # is the run that matters.
    tracing._initialised_pid, tracing._enabled, tracing._provider = None, False, None

    assert tracing.is_enabled() is False
    for _ in range(100):
        assert ctx_mod.current_trace_ids() == (None, None)

    assert reached == [], "the propagator was consulted with tracing off"


def test_a_linked_root_is_a_genuine_root(tracing_state, tmp_path):
    """`link_to` means link *instead of* parent, so it detaches.

    The premise used to be "the scheduling span has already ended, so there is
    nothing to inherit". Driving it showed the opposite: Starlette copies the
    request's contextvars into the worker thread, so this came out a child two
    levels deep with its Link pointing at its own grandparent.
    """
    out = _run_isolated(
        """
        from opentelemetry import trace
        import tracing, tracing.spans as S
        tracing.init_tracing()
        with trace.get_tracer("t").start_as_current_span("POST /ondemand") as req:
            captured = tracing.current_span_context()
            # Opened while the request context is still live and current.
            h = S.open_root_span({"sensorId": "cam-1"}, pipeline_mode="ondemand",
                                 link_to=captured)
            own = format(h._span.get_span_context().trace_id, "032x")
            links = [format(l.context.span_id, "016x") for l in (h._span.links or [])]
            parent = h._span.parent
            h.close(None, None); h.detach()
        print(parent is None,
              own != format(req.get_span_context().trace_id, "032x"),
              format(req.get_span_context().span_id, "016x") in links)
        """,
        {"ENABLE_OTEL_MONITORING": "true"}, tmp_path, sampling_ratio=1.0,
    )
    is_root, own_trace, links_back = out.split()
    assert is_root == "True", "the linked root inherited an ambient parent"
    assert own_trace == "True", "it did not start its own trace"
    assert links_back == "True", "the link does not point at the scheduling span"


@pytest.mark.parametrize("message", [None, "a string", 42, [], object()])
def test_open_root_span_survives_any_message(tracing_state, message):
    """The prefix's only contact with caller data, and it runs before the guard.

    So it has to be non-raising for anything a caller can hand it, not just for
    the dict the pipeline normally passes — that is what lets the span be opened
    before the try without leaving a gap.
    """
    # The property is "never raises", not "returns None". With tracing enabled a
    # falsy message is perfectly valid — `manual_attributes` simply lifts no
    # fields from it — so asserting None here passed only because the run had the
    # feature off, which is the run that proves the least.
    handle = spans_mod.open_root_span(
        message, pipeline_mode="event_loop", timestamps={"kafkaPublishedAt": None}
    )
    if handle is not None:
        handle.detach()


def test_duration_histograms_can_resolve_a_percentile():
    """The SDK's default boundaries are shaped for milliseconds.

    These instruments declare `unit="s"` and record seconds, so against the
    default every alert from 3ms to 1.5s landed in the single `(0, 5]` bucket and
    no percentile was recoverable — the same failure as the Prometheus histograms
    whose smallest bucket is 1.0s, arrived at from the opposite direction.

    Asserted as "values spanning the realistic range occupy distinct buckets"
    rather than by pinning the boundary list, so the numbers can be tuned without
    the test becoming a copy of the code.
    """
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View

    from tracing.meters import _views

    reader = InMemoryMetricReader()
    provider = MeterProvider(
        metric_readers=[reader],
        views=_views(ExplicitBucketHistogramAggregation, View),
    )
    meter = provider.get_meter("test")

    samples = {
        "alert.verification.duration": [0.0031, 0.2, 0.6, 1.5, 12.0],
        "alert.capacity.wait.duration": [0.002, 0.03, 0.4, 3.0],
    }
    for name, values in samples.items():
        instrument = meter.create_histogram(name, unit="s")
        for v in values:
            instrument.record(v)

    seen = {}
    for rm in reader.get_metrics_data().resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                point = list(metric.data.data_points)[0]
                seen[metric.name] = sum(1 for c in point.bucket_counts if c)

    try:
        for name, values in samples.items():
            assert seen.get(name) == len(values), (
                f"{name}: {len(values)} values spanning the range fell into "
                f"{seen.get(name)} bucket(s) — percentiles are not recoverable"
            )
    finally:
        provider.shutdown()


def test_the_shipped_instruments_get_those_buckets(tmp_path):
    """The test above proves ``_views()`` is right; this proves it is used.

    Both survive mutations that reintroduce the original defect -- dropping
    ``views=`` from the ``MeterProvider(...)`` call, or renaming a production
    instrument so no view matches it -- because they exercise ``_views`` in
    isolation against hand-copied names. So the fix was guarded at the helper
    and unguarded at the wiring, which is the half that ships.

    Drives the real ``init_metrics()`` and records through the real public
    functions, swapping only the reader so the points can be read back. In a
    subprocess for the same reason as everything else here: ``init_metrics``
    sets the process-global MeterProvider, which OpenTelemetry refuses to
    replace, so running it in-process would leave a shut-down provider installed
    for every test after it.
    """
    out = _run_isolated(
        """
        from opentelemetry.sdk.metrics import export as export_mod
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from tracing import meters

        reader = InMemoryMetricReader()
        export_mod.PeriodicExportingMetricReader = lambda *a, **k: reader

        assert meters.init_metrics("test-wiring") is True
        for v in (0.0031, 0.2, 0.6, 1.5, 12.0):
            meters.observe_verification_duration(v, pipeline_mode="event_loop")
        for v in (0.002, 0.03, 0.4, 3.0):
            meters.observe_capacity_wait(seconds=v, service="vlm")

        spread = {}
        for rm in reader.get_metrics_data().resource_metrics:
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    point = metric.data.data_points[0]
                    if hasattr(point, "bucket_counts"):
                        spread[metric.name] = sum(1 for c in point.bucket_counts if c)
        meters.shutdown()
        print(sorted(spread.items()))
        """,
        # console, so nothing dials a collector; the reader is swapped anyway.
        {"OTEL_METRICS_EXPORTER": "console"},
        tmp_path,
    )
    assert out == "[('alert.capacity.wait.duration', 4), ('alert.verification.duration', 5)]", (
        f"the shipped instruments did not get the views: {out}. Either views= was "
        "dropped from MeterProvider(...) or an instrument name no longer matches "
        "the view that targets it."
    )


# --------------------------------------------------------------------------
# Guards for the round-6 fixes. Each of these was shipped unguarded first, and
# reverting it left all 3366 tests green -- which on this feature is how a fix
# survives one round and is quietly undone the next.
# --------------------------------------------------------------------------


def test_identifiers_are_capped_before_they_reach_a_span():
    """Producer-controlled fields arrive from Kafka unvalidated.

    `AlertRequestEntity`'s `max_length=256` guards the REST surface only, and
    the SDK leaves `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` unlimited by default, so
    nothing downstream trims them either. Measured before the cap: two fields
    produced 250 KB of attributes on every span of one alert.
    """
    from tracing.attributes import MAX_IDENTIFIER_CHARS, manual_attributes

    attrs = manual_attributes(
        {"sensorId": "X" * 200_000, "category": "C" * 50_000, "correlationId": "ok-1"},
        include_content=False,
    )
    assert len(attrs["sensorId"]) == MAX_IDENTIFIER_CHARS
    assert len(attrs["category"]) == MAX_IDENTIFIER_CHARS
    # Real values pass through untouched; the cap is a ceiling, not a formatter.
    assert attrs["correlationId"] == "ok-1"


def test_the_content_budget_is_not_clamped_by_the_identifier_cap():
    """`max_content_chars` is the operator's budget and must be honoured above 1024.

    Applying both caps truncated the *output* of the first: 5000 chars in, 1024
    out, and the marker counted from the intermediate -- claiming 3088 dropped
    where 3976 were. Same defect `truncate`'s own tests exist to prevent, one
    layer up.
    """
    import re

    from tracing.attributes import manual_attributes

    for budget in (512, 2000, 4096):
        attrs = manual_attributes(
            None, include_content=True, max_content_chars=budget,
            **{"video.url": "P" * 5000},
        )
        value = attrs["video.url"]
        assert len(value) == budget, f"budget {budget} produced {len(value)}"
        # The marker counts from the original, and is itself inside the budget:
        # kept text + dropped == the input length. Counting from the truncated
        # intermediate instead is exactly the double-truncation defect.
        dropped = int(re.search(r"\.\.\.\[\+(\d+) chars\]$", value).group(1))
        marker_len = len(f"...[+{dropped} chars]")
        assert (len(value) - marker_len) + dropped == 5000, (
            f"budget {budget}: kept {len(value) - marker_len} + reported {dropped} "
            f"!= 5000 — the marker is counting from the wrong string"
        )


def test_the_root_span_caps_verdict_and_error_reason():
    """The two largest producer-controlled fields are written past `_put`.

    `RootSpanHandle.decorate`/`close` call `set_attribute` directly, so the cap
    on the identifiers left these two uncapped -- 501 KB on the span where the
    fix claimed 250 KB had been removed.
    """
    from tracing.attributes import MAX_IDENTIFIER_CHARS
    from tracing.spans import RootSpanHandle

    written = {}

    class _Span:
        def is_recording(self):
            return True

        def set_attribute(self, key, value):
            written[key] = value

        def end(self, *a, **k):
            pass

    handle = RootSpanHandle(_Span(), None, None)
    handle.decorate(None, {"info": {"verdict": "V" * 200_000}}, "R" * 200_000)

    assert len(written["verdict"]) == MAX_IDENTIFIER_CHARS
    assert len(written["error_reason"]) == MAX_IDENTIFIER_CHARS


def test_metrics_exporter_falls_back_to_the_traces_exporter_in_compose():
    """`OTEL_TRACES_EXPORTER=none` is the documented lever for a dead collector.

    `meters.py` chains the two variables so that lever silences metrics too --
    without it, metrics keep retrying OTLP and block ~7s at exit against docker
    stop's 10s grace. Enumerating both in compose with independent defaults made
    `OTEL_METRICS_EXPORTER` always explicitly `otlp`, so the chain could never
    fire and the lever did nothing. Asserted on the rendered default expression
    because no unit test can run `docker compose config`.
    """
    compose = _REPO / "deploy/docker/services/alert/compose.yml"
    text = compose.read_text()
    assert "OTEL_METRICS_EXPORTER: ${ALERT_OTEL_METRICS_EXPORTER:-${ALERT_OTEL_TRACES_EXPORTER:-otlp}}" in text, (
        "OTEL_METRICS_EXPORTER must fall back to ALERT_OTEL_TRACES_EXPORTER; an "
        "independent default disables the lever meters.py documents"
    )
    # Not an empty default: Compose renders '' and meters.py reads '' as off,
    # which would silently disable metrics on every deployment.
    assert "OTEL_METRICS_EXPORTER: ${ALERT_OTEL_METRICS_EXPORTER:-}" not in text


def test_the_single_process_startup_initialises_tracing_eagerly():
    """Every shipped config sets `processes: 1`.

    The other `init_tracing()` lives in `_run_pipeline_process`, which only runs
    in a spawned child, so on the shipped default the ~160ms of imports, config
    read and HTTP-client patching landed inside the first alert on the event
    loop thread -- exactly what the eager call exists to avoid.
    """
    import ast

    entrypoint = _REPO / "services/alert/enhance_alert_with_vlm.py"
    tree = ast.parse(entrypoint.read_text())

    single_process_branches = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and getattr(node.test.operand, "id", None) == "multi_process"
    ]
    assert single_process_branches, "the `if not multi_process:` branch moved"

    def inits(nodes):
        return any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "init_tracing"
            for stmt in nodes for n in ast.walk(stmt)
        )

    assert any(inits(branch.body) for branch in single_process_branches), (
        "no init_tracing() in the single-process branch; the shipped default "
        "would initialise lazily, inside the first alert"
    )


def test_the_otel_verdict_is_normalised_like_the_prometheus_one(monkeypatch):
    """Raw `info.verdict` is producer-controlled and unbounded.

    It reaches `record_event_complete` unvalidated on the early-return paths
    (`no_prompt`, `malformed_message`), before the VLM has run. Unnormalised it
    would mint a new attribute set per distinct string -- permanent, at the
    collector -- and would disagree with `EVENTS_TOTAL`, which records
    "unknown" for the same event.
    """
    from metrics import recorder

    seen = {}

    def _capture(seconds, pipeline_mode=None, verdict=None):
        seen["verdict"] = verdict

    monkeypatch.setattr(recorder._otel_meters, "_instruments", {"verification_duration": object()})
    monkeypatch.setattr(recorder._otel_meters, "observe_verification_duration", _capture)

    recorder._observe_verification_duration_otel(
        {"end": "2020-01-01T00:00:00+00:00", "info": {"verdict": "upstream-freeform"}},
        "event_loop",
    )
    assert seen["verdict"] == "unknown", (
        f"raw verdict {seen['verdict']!r} reached the OTel histogram; "
        "Prometheus would have recorded 'unknown' for the same event"
    )


def test_the_otel_observation_does_no_work_when_metrics_are_off(monkeypatch):
    """It runs above the PROMETHEUS_ENABLED gate, so it must check its own.

    `_normalize_verdict` is neither free nor pure: it warns once per distinct
    unrecognised value and retains it in a process-global set. Evaluated as an
    argument, it ran before `_record` could discover the instrument was absent
    -- so a producer emitting a unique verdict per event leaked one string per
    event on a deployment recording nothing at all.
    """
    from metrics import recorder

    monkeypatch.setattr(recorder._otel_meters, "_instruments", {})
    monkeypatch.setattr(recorder, "_UNKNOWN_VERDICTS_SEEN", set())

    for i in range(5):
        recorder._observe_verification_duration_otel(
            {"end": "2020-01-01T00:00:00+00:00", "info": {"verdict": f"freeform-{i}"}},
            "event_loop",
        )

    assert not recorder._UNKNOWN_VERDICTS_SEEN, (
        f"{len(recorder._UNKNOWN_VERDICTS_SEEN)} verdicts retained with no "
        "instruments to record them against"
    )


@pytest.mark.parametrize("length,budget", [
    (1005, 1000),   # the digit-width case: one pass reported 18 dropped, 19 were
    (1030, 1024),   # the same at MAX_IDENTIFIER_CHARS
    (5000, 512),    # the shipped budget
    (10000, 100),
    (200000, 1024),
])
def test_truncate_reports_what_it_actually_dropped(length, budget):
    """Two invariants, and the second used to fail on a digit-width boundary.

    The kept prefix depends on how many digits the reported count needs, and the
    count depends on the prefix -- so a single pass could report a number
    computed against a different prefix than the one returned. `max_chars` stays
    a hard ceiling either way; what broke was the claim.
    """
    import re

    from tracing.attributes import truncate

    out = truncate("P" * length, budget)
    assert len(out) <= budget, f"{len(out)} exceeds the ceiling {budget}"

    match = re.search(r"\.\.\.\[\+(\d+) chars\]$", out)
    if match is None:
        return  # budget too small to hold any suffix; a bare cut is the contract
    reported = int(match.group(1))
    kept = len(out) - len(f"...[+{reported} chars]")
    assert kept + reported == length, (
        f"kept {kept} + reported {reported} != {length} — the marker is counting "
        "against a prefix other than the one returned"
    )


def test_every_tracing_key_the_chart_reads_is_declared_in_values():
    """Close the class, not the instance.

    The template read six `tracing.*` keys while values.yaml declared three, so
    three levers worked and none of them were greppable -- an operator looking
    for the knob the code documents found nothing. Adding the three fixes that
    instance; this stops key seven arriving the same way, which is the same move
    that replaced the hand-written config list with `git ls-files`.

    values.yaml is the discovery surface, so a key being *reachable* through
    `(.Values.tracing).x | default ...` is not enough.
    """
    import re

    template = _REPO / "deploy/helm/services/alert/templates/deployment.yaml"
    values = _REPO / "deploy/helm/services/alert/values.yaml"
    assert template.exists() and values.exists(), "chart layout moved; this test is blind"

    read = set(re.findall(r"\(\.Values\.tracing\)\.(\w+)", template.read_text()))
    assert read, "no tracing keys found in the template; the accessor style changed"

    block = re.search(r"^tracing:\n((?:[ \t]+.*\n|\n)*)", values.read_text(), re.M)
    assert block, "no tracing block in values.yaml"
    declared = set(re.findall(r"^\s+(\w+):", block.group(1), re.M))

    assert read <= declared, (
        "the chart reads tracing keys that values.yaml does not declare, so they "
        f"work but cannot be found: {sorted(read - declared)}"
    )

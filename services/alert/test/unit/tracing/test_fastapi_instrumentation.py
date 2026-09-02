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

"""Server-side tracing on the API app (REQ-005, REQ-008).

Driven through a real ``TestClient`` against a real instrumented app, because
the two things worth asserting here — that an inbound ``traceparent`` actually
becomes the parent, and that probe endpoints actually produce nothing — are
properties of the request path, and a structural test of the install call would
pass whether or not either holds.

Each test builds its own app and its own provider. Instrumentation is global
per-app, so a shared app would carry one test's middleware into the next.
"""

import pathlib

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import tracing

# A syntactically valid W3C traceparent: version 00, then trace id, span id,
# sampled flag.
_TRACE_ID_HEX = "4bf92f3577b34da6a3ce929d0e0e4736"
_TRACEPARENT = f"00-{_TRACE_ID_HEX}-00f067aa0ba902b7-01"


@pytest.fixture
def app_and_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    app = FastAPI()

    # Mounted through include_router, NOT declared on the app with @app.get.
    # This is not incidental: all 11 AB routes are mounted this way
    # (web/main.py registers five routers), and the two shapes take different
    # paths through the instrumentation's route resolution. An earlier version
    # of this fixture used @app.get and stayed green against an
    # instrumentation/fastapi pairing that returned HTTP 500 for every real
    # route in the service.
    router = APIRouter(prefix="/api/v1/realtime")

    @router.get("")
    async def list_rules():
        return {"rules": []}

    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    installed = tracing.instrument_fastapi_app(app, tracer_provider=provider)
    assert installed, "the fixture depends on the instrumentation being installed"
    yield app, exporter
    provider.shutdown()


def test_a_request_produces_a_server_span(app_and_exporter):
    app, exporter = app_and_exporter
    with TestClient(app) as client:
        assert client.get("/api/v1/realtime").status_code == 200

    spans = exporter.get_finished_spans()
    # Exactly one: the ASGI layer's own "http send"/"http receive" spans are
    # excluded, so a request costs one span rather than three.
    assert len(spans) == 1
    assert spans[0].kind.name == "SERVER"


def test_the_span_carries_the_caller_user_agent(app_and_exporter):
    """REQ-005's deliverable: telling a skill's curl from the UI's request.

    Both attribute spellings are accepted. Which one is emitted depends on
    OTEL_SEMCONV_STABILITY_OPT_IN, which init_tracing pins to http/dup but a
    deployment may have set itself — so a test demanding one name would fail on
    a legitimate configuration.
    """
    app, exporter = app_and_exporter
    with TestClient(app) as client:
        client.get("/api/v1/realtime", headers={"user-agent": "vss-manage-alerts/3.3.3"})

    attributes = exporter.get_finished_spans()[0].attributes
    agent = attributes.get("http.user_agent") or attributes.get("user_agent.original")
    assert agent == "vss-manage-alerts/3.3.3"


def test_inbound_traceparent_becomes_the_parent(app_and_exporter):
    """REQ-008. Without this the caller's trace and AB's are two traces."""
    app, exporter = app_and_exporter
    with TestClient(app) as client:
        client.get("/api/v1/realtime", headers={"traceparent": _TRACEPARENT})

    span = exporter.get_finished_spans()[0]
    assert format(span.context.trace_id, "032x") == _TRACE_ID_HEX
    assert span.parent is not None


def test_a_request_without_traceparent_starts_a_fresh_trace(app_and_exporter):
    app, exporter = app_and_exporter
    with TestClient(app) as client:
        client.get("/api/v1/realtime")

    span = exporter.get_finished_spans()[0]
    assert format(span.context.trace_id, "032x") != _TRACE_ID_HEX
    assert span.parent is None


def test_probe_endpoints_produce_no_spans(app_and_exporter):
    """Health checks poll forever; a span each would drown the alert traffic."""
    app, exporter = app_and_exporter
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert exporter.get_finished_spans() == ()


@pytest.mark.parametrize(
    "url,excluded",
    [
        ("/health", True), ("/ready", True), ("/metrics", True),
        ("/health?verbose=1", True),
        # AB paths carry operator-chosen sensor and rule ids. Unanchored
        # substring matching silently dropped these, which is the worst possible
        # failure mode for a probe filter — the traffic just is not there.
        ("/api/v1/alerts/health-check-cam", False),
        ("/api/v1/alerts/warehouse-metrics-3", False),
        ("/healthz", False),
        ("/api/v1/realtime", False),
    ],
)
def test_excluded_urls_matches_probes_and_nothing_else(url, excluded):
    """``/ready`` arrives with the multi-core work and must be excluded too."""
    import re

    patterns = tracing._EXCLUDED_URLS.split(",")
    assert any(re.search(p, url) for p in patterns) is excluded


def test_instrumentation_is_skipped_when_tracing_is_off(monkeypatch):
    """With tracing off the app must be the app it is today — no middleware.

    The premise is cleared explicitly rather than inherited from however the
    suite was invoked: this passed alone and failed in a run with the feature
    enabled, which is the run that matters.
    """
    monkeypatch.delenv("ENABLE_OTEL_MONITORING", raising=False)
    tracing._initialised_pid, tracing._enabled, tracing._provider = None, False, None
    app = FastAPI()
    before = len(app.user_middleware)

    assert tracing.is_enabled() is False
    assert tracing.instrument_fastapi_app(app) is False
    assert len(app.user_middleware) == before


def test_instrumentation_failure_does_not_break_the_app(monkeypatch):
    """A tracing import problem must cost spans, never the API."""
    import builtins

    real_import = builtins.__import__

    def explode(name, *args, **kwargs):
        if "instrumentation.fastapi" in name:
            raise ImportError("simulated missing package")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", explode)
    provider = TracerProvider()
    assert tracing.instrument_fastapi_app(FastAPI(), tracer_provider=provider) is False


def test_the_instrumentation_matches_the_pinned_fastapi(app_and_exporter):
    """A route mounted via include_router must actually serve, not 500.

    The regression guard for the requirements pin, and the home for what that
    pin depends on — requirements.txt is a flat version list and carries no
    comments.

    `opentelemetry-instrumentation-fastapi` is pinned exactly while `fastapi` is
    not (`>=0.68.0`, no ceiling), so a resolution that moves fastapi past what the
    instrumentation understands breaks route resolution inside the ASGI
    middleware — at request time, where `instrument_fastapi_app`'s try/except
    (which guards installation only) cannot contain it. The symptom is HTTP 500
    on every AB route the moment tracing is switched on, and every AB route is
    mounted with `include_router`.

    That is not hypothetical: at `0.57b0`, `_get_route_details` read `.path` off a
    `_IncludedRouter` and raised `AttributeError`. Those pins were copied from
    `services/rtvi/rt-embed` as "already vetted" — but rt-embed also pins
    `fastapi==0.121.3`, which was the half that made them valid.

    Two rules follow. The SDK and the instrumentation share a release train
    (`1.44.0` <-> `0.65b0`); bump them together, mixing trains is unsupported. And
    if this test fails after a dependency update, the pairing is what broke, not
    the app.
    """
    app, exporter = app_and_exporter
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/realtime")

    assert response.status_code == 200, (
        "include_router route failed under the instrumented app — check that the "
        "opentelemetry-instrumentation-* pins still match the resolved fastapi/starlette"
    )
    assert exporter.get_finished_spans()[0].attributes.get("http.route") == "/api/v1/realtime"


def test_helm_chart_renders_with_a_null_tracing_map():
    """`--set tracing=null` must default off, not fail to render.

    Helm 3 removes a map's defaults on an explicit null, so an unparenthesised
    `.Values.tracing.enabled` is a nil dereference and the whole chart fails —
    which is what a parent chart nulling the map would hit.
    """
    import shutil
    import subprocess

    if shutil.which("helm") is None:
        pytest.skip("helm not installed")

    chart = pathlib.Path(__file__).resolve().parents[5] / "deploy/helm/services/alert"
    result = subprocess.run(
        ["helm", "template", str(chart), "--set", "enabled=true", "--set", "tracing=null"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"chart failed to render:\n{result.stderr}"
    assert 'name: ENABLE_OTEL_MONITORING' in result.stdout
    assert '"false"' in result.stdout

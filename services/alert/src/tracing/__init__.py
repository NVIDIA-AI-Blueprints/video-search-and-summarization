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

"""OpenTelemetry tracing for Alert Bridge — the one place ``opentelemetry`` is imported.

Design constraints this module exists to satisfy:

* **Soft dependency (REQ-016).** Every OTel import is lazy and guarded. With the
  packages absent, or with ``ENABLE_OTEL_MONITORING`` unset, the service imports
  and runs exactly as before — no export attempted, no span created, no latency
  added. That is the default in every shipped profile.
* **Per-process, PID-keyed (REQ-020).** Alert Bridge runs a fleet of processes.
  Provider state cannot be inherited across a process boundary, so init is keyed
  on the current pid: a call from a process that has not initialised yet builds
  its own provider, whatever the parent did.
* **PII (REQ-014).** Manually-built spans are gated at creation by
  ``attributes.py``. Auto-instrumented spans are not reachable that way, so a
  :class:`SanitizingSpanExporter` wraps whichever exporter is configured and
  scrubs on the way out — the only mechanism that covers *every* exported span.

The gate is independent of ``PROMETHEUS_METRICS_ENABLED``. The two telemetry
systems are separate: neither may gate the other.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

# Re-exported so the logging layer has one import point for tracing (REQ-016)
# and never reaches into a submodule.
from .context import current_span_context, current_trace_ids

logger = logging.getLogger(__name__)

# Attribute names that can carry a credential in a query string. All four are
# live in the pinned instrumentation: the old semconv pair (`http.url`,
# `http.target`) and the new one (`url.full`, `url.query`).
_URL_FULL_KEYS = ("http.url", "url.full")
_URL_PATH_KEYS = ("http.target",)
_URL_QUERY_KEYS = ("url.query",)

_EXCEPTION_KEYS = ("exception.message", "exception.stacktrace")

_ENABLE_ENV = "ENABLE_OTEL_MONITORING"
# Kept in step with the tracing block every deployment config ships.
DEFAULT_SAMPLING_RATIO = 0.1
# Both directions. The server pair was covered from the start; the client pair
# was not, and it is arguably the worse omission - outbound headers are where
# AB's own credentials to VST and the VLM live, so capture there attaches them
# to a span rather than merely echoing a caller's.
_CAPTURE_HEADER_ENVS = (
    "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST",
    "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_RESPONSE",
    "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST",
    "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_RESPONSE",
)

# Process-local state. Keyed on pid so a child never inherits a parent's view.
_initialised_pid: Optional[int] = None
# Resolved once per process by init_tracing() so every manual span site reads the
# same policy without threading it through call after call.
_content_policy: Tuple[bool, int] = (False, 512)
# init_tracing() is reached from open_root_span, so several pipeline coroutines
# can arrive at once on a process's first alert. Without this, two of them pass
# the pid check together and each builds a provider: one wins
# set_tracer_provider, the other is orphaned with a live export thread that can
# never deliver. Measured before the lock existed - 32 racing threads produced
# two providers.
_init_lock = threading.Lock()
_enabled: bool = False
_provider: Any = None



_SAMPLER_ENVS = ("OTEL_TRACES_SAMPLER", "OTEL_TRACES_SAMPLER_ARG")


def _tracing_config() -> Dict[str, Any]:
    """Read ``alert_agent.tracing`` from config.yaml. ``{}`` on any problem.

    Tracing must never be the reason the service fails to start, so a missing
    file, unreadable YAML or absent block all degrade to the defaults rather
    than raising.
    """
    try:
        from utils.config import load_config

        block = (load_config(default_on_missing=True).get("alert_agent") or {}).get("tracing")
        return block if isinstance(block, dict) else {}
    except Exception:
        logger.debug("could not read alert_agent.tracing; using defaults", exc_info=True)
        return {}


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _as_bool(value: Any) -> bool:
    """Coerce a YAML scalar to a bool, failing closed.

    ``bool`` is the wrong cast here and was a live PII hazard:
    ``bool("false") is True``, so ``include_content: 'false'`` - one pair of
    quotes, invisible in a diff - turned prompt text, VLM responses and presigned
    video URLs back on. And because ``bool()`` never raises, the "not usable"
    warning below could not fire for it.

    Anything unrecognised is False with a warning. A content gate that fails open
    is worse than no gate.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    # Everything else numeric falls through to the string path and gets the
    # warning. `value != 0` used to accept 2, -1, 0.5 and .nan as True - the
    # same one-character-typo class as the quoted 'false' this function was
    # written for, and NaN in particular failed closed for sampling while
    # failing open for PII.
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text not in _FALSE:
        logger.warning("alert_agent.tracing: %r is not a boolean; treating it as false", value)
    return False


def _as_ratio(value: Any) -> float:
    """Parse a sampling ratio and clamp it into range.

    Out of range used to reach ``TraceIdRatioBased``, which raises, which the
    caller's broad except turned into "tracing initialisation failed" - so an
    operator writing ``50`` for "50 percent" got tracing entirely off, with a
    message pointing at the SDK rather than at their value.
    """
    ratio = float(value)
    if ratio != ratio or ratio in (float("inf"), float("-inf")):
        # Not a range problem, so do not clamp it into range: `.inf` clamped up
        # gives 100% sampling, which is exactly what REQ-015 forbids and the
        # same fail-open shape as reading a non-boolean as True. Raise so
        # _resolve falls back to the default and says so.
        raise ValueError(f"sampling_ratio {value!r} is not a finite number")
    if not 0.0 <= ratio <= 1.0:
        clamped = min(1.0, max(0.0, ratio))
        logger.warning(
            "alert_agent.tracing.sampling_ratio %s is outside 0.0-1.0; using %s", ratio, clamped
        )
        return clamped
    return ratio


def _as_chars(value: Any) -> int:
    """Content budget: a negative value is rejected, not read as unbounded."""
    chars = int(value)
    if chars <= 0:
        # 0 is not "no limit" and not a useful limit either: every content
        # attribute becomes an empty string, which `_put` then drops, so the
        # gate reads as open while nothing is emitted.
        logger.warning("alert_agent.tracing.max_content_chars %s is not usable; using 512", chars)
        return 512
    return chars


def _resolve(config: Dict[str, Any], key: str, override: Any, default: Any, cast) -> Any:
    """An explicit argument wins over config, config wins over the default."""
    try:
        # The override goes through the same cast. It used to be returned raw,
        # so `init_tracing(include_content="false")` re-created the exact PII
        # bug the casts exist to prevent, one call path over - and this is a
        # documented, exported entry point whose obvious future caller is the
        # eager initialisation at the pipeline process entry.
        if override is not None:
            return cast(override)
        if key in config and config[key] is not None:
            return cast(config[key])
    except Exception:
        logger.warning("alert_agent.tracing.%s is not usable; using %r", key, default)
    return default


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _strip_query(value: Any) -> Any:
    """Drop everything from ``?`` onward, preserving scheme, host and path."""
    if not isinstance(value, str) or "?" not in value:
        return value
    try:
        parts = urlsplit(value)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return value.split("?", 1)[0]


def sanitize_attributes(attributes: Optional[Dict[str, Any]],
                        include_content: bool = False) -> Optional[Dict[str, Any]]:
    """Apply the REQ-014 redaction rules to one attribute mapping.

    Two rules, because the four URL-shaped names do not hold the same shape and
    a single generic transform is a no-op on two of them:

    * ``http.url`` / ``url.full`` are absolute URIs → drop from ``?``.
    * ``http.target`` is a path (the ASGI instrumentation passes ``path`` as the
      target, so it carries no query today) → drop from ``?`` defensively.
    * ``url.query`` is the **bare query string** — not a URL. Stripping "the
      query" from it leaves it untouched, so it is blanked outright.
    * ``exception.message`` / ``exception.stacktrace`` are not URL-shaped at all
      and can carry a VST response body; with the content gate closed they are
      reduced to the exception type name.
    """
    if not attributes:
        return attributes
    out = dict(attributes)
    for key in _URL_FULL_KEYS + _URL_PATH_KEYS:
        if key in out:
            out[key] = _strip_query(out[key])
    for key in _URL_QUERY_KEYS:
        if key in out:
            out[key] = ""
    if not include_content:
        exc_type = out.get("exception.type")
        for key in _EXCEPTION_KEYS:
            if key in out:
                out[key] = str(exc_type) if exc_type else "Exception"
    return out


def sanitize_status(status: Any, include_content: bool = False) -> Any:
    """Reduce a span ``Status.description`` to the exception type name.

    The third redaction channel, and the one the first revision missed. The SDK
    fills the description with ``f"{type(exc).__name__}: {exc}"`` whenever a span
    ends on an exception (``use_span(..., set_status_on_exception=True)``, the
    default), and the HTTP client instrumentors write transport errors there too.
    So the same string the attribute and event rules go to some length to keep
    out arrives one field over - an Elasticsearch document built from an alert
    payload, or a presigned VST URL complete with its ``sig`` and ``token`` query
    parameters, both of which reached Jaeger in testing.

    Splitting on the first colon keeps the type name, which is the part that is
    diagnostic, and drops the message, which is the part that is not ours to
    export.
    """
    if include_content or status is None:
        return status
    description = getattr(status, "description", None)
    if not description:
        return status
    try:
        from opentelemetry.trace import Status

        head, sep, _ = description.partition(":")
        # No colon means this is not the "Type: message" shape written by the
        # SDK and the instrumentors, so there is no type name to keep. Fail
        # closed rather than returning the whole string, which is what splitting
        # unconditionally did - the contract here is "reduce to the type name",
        # and for an unrecognised shape the honest answer is that we do not have
        # one.
        return Status(status.status_code, head.strip() if sep else "")
    except Exception:
        logger.debug("status sanitisation failed", exc_info=True)
        return status


class SanitizingSpanExporter:
    """Wraps the configured exporter and scrubs every span on the way out.

    A ``SpanProcessor`` cannot do this: ``on_end`` receives a read-only
    ``ReadableSpan`` and ``set_attribute`` after ``end()`` is a documented no-op,
    so the only hook that reaches *every* span — manual and auto-instrumented
    alike — is the exporter. Mutating the private ``_attributes`` dict from a
    processor does work, and is deliberately not used: it depends on an
    unstable private attribute.

    Wrapping happens around whichever exporter ``OTEL_TRACES_EXPORTER`` selects,
    not OTLP specifically, so a developer debugging with the console exporter is
    not silently unprotected.
    """

    def __init__(self, wrapped, include_content: bool = False):
        self._wrapped = wrapped
        self._include_content = include_content

    def _rebuild(self, span):
        from opentelemetry.sdk.trace import ReadableSpan

        attributes = sanitize_attributes(dict(span.attributes or {}), self._include_content)

        events = span.events
        if events:
            from opentelemetry.sdk.trace import Event

            events = tuple(
                Event(
                    name=e.name,
                    attributes=sanitize_attributes(dict(e.attributes or {}), self._include_content),
                    timestamp=e.timestamp,
                )
                for e in events
            )

        # Exactly the 13 real constructor parameters. There is no
        # `set_status_on_exception` parameter; passing one raises TypeError.
        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=attributes,
            events=events,
            links=span.links,
            kind=span.kind,
            # `instrumentation_info` is deprecated since 1.11.1 and merely reading
            # it emits a DeprecationWarning; `instrumentation_scope` supersedes it
            # and the constructor derives the old form from the new one.
            status=sanitize_status(span.status, self._include_content),
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=getattr(span, "instrumentation_scope", None),
        )

    def export(self, spans: Sequence[Any]):
        try:
            spans = [self._rebuild(s) for s in spans]
        except Exception:
            # Never drop telemetry because sanitising failed — but never export
            # unsanitised either. Failing closed on the batch is the safe side.
            logger.warning("span sanitisation failed; dropping batch", exc_info=True)
            from opentelemetry.sdk.trace.export import SpanExportResult

            return SpanExportResult.FAILURE
        return self._wrapped.export(spans)

    def shutdown(self):
        return self._wrapped.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        try:
            return self._wrapped.force_flush(timeout_millis)
        except Exception:
            return True


def _build_exporter(include_content: bool):
    """Construct the exporter ``OTEL_TRACES_EXPORTER`` names, then wrap it."""
    kind = os.getenv("OTEL_TRACES_EXPORTER", "otlp").strip().lower()
    if kind in {"none", "null", ""}:
        return None
    if kind == "console":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        inner = ConsoleSpanExporter()
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # Reads OTEL_EXPORTER_OTLP_ENDPOINT / _TRACES_ENDPOINT from the env.
        inner = OTLPSpanExporter()
    return SanitizingSpanExporter(inner, include_content=include_content)


def _build_sampler(ratio: float):
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    return ParentBased(root=TraceIdRatioBased(ratio))


def _noop_meter_provider():
    """A provider that records nothing, for the instrumentors' own metrics.

    They are useful spans and unspecified metrics; REQ-011 names exactly three
    instruments and the collector's Prometheus exporter carries whatever arrives.
    """
    try:
        from opentelemetry.metrics import NoOpMeterProvider

        return NoOpMeterProvider()
    except Exception:
        return None


def _instrument_http_clients() -> None:
    """Install the outbound HTTP client instrumentors (REQ-009).

    These do two things at once: create a client span per outbound call, and
    inject ``traceparent`` into the request headers so VST and the VLM can join
    the trace once they extract it. Both are left **unconfigured** — no request
    or response hooks — so the "unmodified auto-instrumentation" claim stays
    literally true; PII on the resulting spans is handled downstream by
    :class:`SanitizingSpanExporter`, which is the only hook that reaches every
    exported span.

    Each is installed independently: a missing optional package must cost only
    its own transport, not the other's.
    """
    for module_name, class_name in (
        ("opentelemetry.instrumentation.requests", "RequestsInstrumentor"),
        ("opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"),
    ):
        try:
            module = __import__(module_name, fromlist=[class_name])
            # No meter provider: with a global one set, these emit their own
            # http.client.* series against it. REQ-011 names three instruments,
            # and OTEL_SEMCONV_STABILITY_OPT_IN=http/dup is pinned, so each
            # auto-instrumented metric arrives under both spellings.
            getattr(module, class_name)().instrument(meter_provider=_noop_meter_provider())
        except Exception as exc:
            logger.info("%s not installed (%s); its spans will be absent", class_name, exc)


def init_tracing(
    service_name: str = "vss-alert-ms",
    *,
    sampling_ratio: Optional[float] = None,
    include_content: Optional[bool] = None,
) -> bool:
    """Initialise tracing for **this process**. Idempotent per pid. Never raises.

    Returns whether tracing is active in this process. Safe to call from every
    process entry point; a call from a pid that has already initialised is a
    cheap no-op, and a call from a fresh pid builds that process its own
    provider.
    """
    pid = os.getpid()
    if _initialised_pid == pid:
        return _enabled

    with _init_lock:
        # Re-check under the lock: the winner may have finished between the
        # cheap check above and here.
        if _initialised_pid == pid:
            return _enabled
        try:
            return _init_locked(pid, service_name, sampling_ratio, include_content)
        except BaseException:
            # "Never raises" is the contract, and web/main.py calls this at
            # module scope - an exception here does not degrade tracing, it
            # stops the API process from importing. The inner try covers the
            # SDK work; this covers everything before it, which config parsing
            # made reachable (`max_content_chars: .inf` raised OverflowError,
            # which no narrower except caught).
            logger.warning("tracing initialisation raised; continuing untraced", exc_info=True)
            return False


def _init_locked(
    pid: int,
    service_name: str,
    sampling_ratio: Optional[float],
    include_content: Optional[bool],
) -> bool:
    """Initialise under ``_init_lock``, publishing the pid only when done.

    The pid is what every other caller's fast path reads, so setting it on entry
    would advertise "this process is initialised" while the provider is still
    being built - and a caller arriving in that window took the fast path and was
    told tracing was off. Narrow, but it lands on a process's first alerts, and
    the multi-core work makes process starts routine rather than one-off.
    Demonstrated with a deliberately slowed provider build: the second thread got
    no span.
    """
    global _initialised_pid, _enabled, _provider, _content_policy

    # Whatever state this module carries came from another process and must not
    # be trusted. Cleared here; the pid is published in the finally below.
    _enabled, _provider, _content_policy = False, None, (False, 512)
    try:
        return _init_unlocked_body(service_name, sampling_ratio, include_content)
    finally:
        _initialised_pid = pid


def _init_unlocked_body(
    service_name: str,
    sampling_ratio: Optional[float],
    include_content: Optional[bool],
) -> bool:
    global _enabled, _provider, _content_policy

    if not _env_flag(_ENABLE_ENV):
        logger.debug("tracing disabled (%s not set)", _ENABLE_ENV)
        return False

    config = _tracing_config()
    # 0.1, matching what every deployment config ships. It used to be 1.0, so a
    # deployment whose config lacked the block - which was all of them - got the
    # opposite of the intent from the fallback.
    sampling_ratio = _resolve(config, "sampling_ratio", sampling_ratio, DEFAULT_SAMPLING_RATIO, _as_ratio)
    include_content = _resolve(config, "include_content", include_content, False, _as_bool)
    max_content_chars = _resolve(config, "max_content_chars", None, 512, _as_chars)
    _content_policy = (bool(include_content), int(max_content_chars))
    if include_content:
        logger.warning(
            "alert_agent.tracing.include_content is ON: prompts, VLM responses and "
            "video URLs will be attached to spans. Alert data may contain PII."
        )

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:
        logger.info("tracing requested but OpenTelemetry is unavailable: %s", exc)
        return False

    try:
        # Arbitrary request headers must not be captured (REQ-014). The
        # instrumentation's own documentation offers `X-.*` as the worked
        # example, which is exactly what this forbids.
        for name in _CAPTURE_HEADER_ENVS:
            if name in os.environ:
                logger.warning("unsetting %s: request-header capture is not permitted", name)
                os.environ.pop(name, None)

        # Pin the semconv mode so the user-agent attribute name is stable.
        # Unset emits `http.user_agent`; `http` emits `user_agent.original`
        # instead, which would silently delete the attribute REQ-005 relies on.
        if "OTEL_SEMCONV_STABILITY_OPT_IN" in os.environ:
            logger.warning(
                "OTEL_SEMCONV_STABILITY_OPT_IN is set to %r by the deployment; leaving it. "
                "Caller-attribution queries must match http.user_agent OR user_agent.original, "
                "since which one is emitted depends on this value.",
                os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"],
            )
        os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http/dup")

        # The provider is built here rather than by the SDK's auto-configuration,
        # which means the standard sampler variables do not reach it. Say so
        # instead of letting an operator set one and watch nothing change.
        for name in _SAMPLER_ENVS:
            if name in os.environ:
                logger.warning(
                    "%s is set but not honoured; sampling comes from "
                    "alert_agent.tracing.sampling_ratio (currently %s)",
                    name, sampling_ratio,
                )

        resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", service_name)})
        provider = TracerProvider(resource=resource, sampler=_build_sampler(sampling_ratio))

        exporter = _build_exporter(include_content)
        if exporter is not None:
            provider.add_span_processor(BatchSpanProcessor(exporter))

        trace.set_tracer_provider(provider)
        # After the provider: an instrumentor installed first would bind spans to
        # whatever provider was global at that moment.
        _instrument_http_clients()
        # Additive and independent: a metrics failure must not stop tracing, so
        # this is initialised after the provider is live and its result is not
        # allowed to change what init_tracing returns.
        try:
            from . import meters

            meters.init_metrics(service_name)
        except Exception:
            logger.debug("OTel metrics could not be initialised", exc_info=True)
        _provider, _enabled = provider, True
        logger.info(
            "tracing initialised (pid=%d, exporter=%s, sampling_ratio=%s)",
            os.getpid(),
            os.getenv("OTEL_TRACES_EXPORTER", "otlp"),
            sampling_ratio,
        )
        return True
    except Exception:
        logger.warning("tracing initialisation failed; continuing untraced", exc_info=True)
        _enabled, _provider = False, None
        return False


# Probe endpoints, excluded from tracing. Kubernetes and compose healthchecks
# poll these on a fixed interval forever; a span each would be the large
# majority of all spans and would carry no information about alert traffic.
# ``/ready`` is not on develop yet - it arrives with the multi-core work - and
# is named here so the exclusion covers it the moment it does.
# Anchored on the path segment, with an optional query string. Unanchored, OTel
# matches these as substrings anywhere in the URL - and AB paths carry
# operator-chosen sensor and rule ids, so /api/v1/alerts/health-check-cam and
# .../warehouse-metrics-3 both produced no span at all. Silent absence is the
# worst failure mode for a probe filter.
_EXCLUDED_URLS = r"/health(\?|$),/ready(\?|$),/metrics(\?|$)"


def instrument_fastapi_app(app, tracer_provider: Any = None) -> bool:
    """Install server-side tracing on the API app (REQ-005, REQ-008).

    Returns True when the instrumentation was installed.

    This is a no-op unless :func:`init_tracing` already succeeded **in this
    process**, and that is deliberate rather than defensive: instrumenting adds
    ASGI middleware, which changes the request path for every caller. With
    tracing off, the app should be byte-for-byte the app it is today. The check
    also keeps the unit suite - which imports ``web.main`` without initialising
    tracing - on the uninstrumented app.

    ``tracer_provider`` overrides the global provider, and passing one also
    stands in for the enabled check - a caller holding a provider has already
    decided. Production passes nothing and takes the global one.

    REQ-008 needs no code here: the ASGI instrumentation extracts an inbound
    ``traceparent`` through the global propagator on its own, so a caller's
    trace continues into AB rather than a new one starting.

    A request that arrives without a ``traceparent`` starts a fresh trace, and a
    probe endpoint produces nothing at all.
    """
    if tracer_provider is None and not is_enabled():
        return False
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            meter_provider=_noop_meter_provider(),
            excluded_urls=_EXCLUDED_URLS,
            tracer_provider=tracer_provider,
            # Without this the ASGI layer emits two INTERNAL "http send" spans
            # per request alongside the server span - three times the volume,
            # describing how the response was written rather than anything
            # about the request. Measured, not assumed: a single GET produced
            # exactly those three spans before this was added.
            exclude_spans=["send", "receive"],
        )
        return True
    except Exception as exc:
        logger.info("FastAPI instrumentation not installed (%s); no server spans", exc)
        return False


def content_policy() -> Tuple[bool, int]:
    """``(include_content, max_content_chars)`` as resolved for this process.

    The single place manual span sites read the content gate from. Threading it
    through every call instead is what let ``live_span`` and ``traced_io`` end up
    outside the policy entirely.
    """
    return _content_policy


def ensure_initialised() -> bool:
    """Initialise tracing for this process if it has not been, and report state.

    Returns whether tracing is active here.

    Exists because the pipeline process has no entry point this change may edit
    yet: REQ-020 puts the call in ``enhance_alert_with_vlm.py``, and the
    multi-core work rewrites that whole region, so an edit there would conflict
    on arrival. Without it the API process initialised and the pipeline process
    did not, which is worse than tracing being off - the operator switches the
    feature on, sees REST spans, and concludes the pipeline is silent rather than
    uninstrumented.

    Calling it from the root-span opener also survives the process model better
    than a single call in ``__main__`` would. ``init_tracing`` is keyed on pid, so
    a worker that inherited an initialised parent's module state would see a stale
    pid and refuse to consider itself enabled. The pipeline fleet spawns rather
    than forks today, so nothing is inherited and this is defensive -- it stays
    because a change of start method would make it load-bearing again, silently.

    Cheap after the first call in a process: a pid comparison.
    """
    return init_tracing()


def is_enabled() -> bool:
    """True when tracing is active **in this process**."""
    return _enabled and _initialised_pid == os.getpid()


def shutdown(timeout_millis: int = 5000) -> None:
    """Flush and stop the providers. Safe to call when never initialised."""
    global _enabled, _provider
    try:
        from . import meters

        meters.shutdown(timeout_millis)
    except Exception:
        logger.debug("metrics shutdown failed", exc_info=True)
    provider, _provider, _enabled = _provider, None, False
    if provider is None:
        return
    try:
        provider.force_flush(timeout_millis)
        provider.shutdown()
    except Exception:
        logger.debug("tracing shutdown failed", exc_info=True)


__all__ = [
    "SanitizingSpanExporter",
    "content_policy",
    "current_span_context",
    "current_trace_ids",
    "ensure_initialised",
    "init_tracing",
    "instrument_fastapi_app",
    "is_enabled",
    "sanitize_attributes",
    "sanitize_status",
    "shutdown",
]

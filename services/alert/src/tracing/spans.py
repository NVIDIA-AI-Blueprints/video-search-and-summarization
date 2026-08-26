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

"""Root span lifecycle for one alert-verification event (REQ-001..REQ-004).

The whole point of this module is that **the span always closes, exactly once**,
on every path the pipeline can take — including the ones that never reach the
metrics recorder. Getting that wrong is the defect class this design was built
against: a span that is opened and never ended is never exported, and every child
under it is orphaned.

**Two closers, both wired.** The pipeline's outermost ``finally`` closes an
event that completed inline; the sink-completion callback closes one whose
publish was deferred to the sink executor. They hand off through one lock, and
``mark_deferred()`` is a *conditional* transition -- it refuses if the callback
has already run, so the span can never be left with nobody to close it.
``mark_deferred()`` is announced before the callback is attached, because a
resolved future runs it synchronously.

**The recorder decorates, it never closes.** ``record_event_complete()`` calls
``decorate()`` as its first statement, above its own Prometheus gate: it is the
only site that knows *why* an event ended, and closing there would land before
post-publish enrichment and truncate the root.

Both are live on all three pipeline modes. An earlier revision of this docstring
described them as forward-wiring with no production caller; that was true for
exactly one revision and is recorded here only because a reader who saw it would
have concluded the machinery was inert at the moment it became load-bearing.

The state machine spans two threads (the pipeline thread and the sink executor),
so every transition is taken under a re-entrant lock. See the concurrency
contract in the feature HLD for the field-by-field table.
"""

from __future__ import annotations

import functools
import inspect
import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple

from . import content_policy, ensure_initialised
from .attributes import (
    MAX_IDENTIFIER_CHARS,
    manual_attributes,
    truncate,
    verdict_of,
)

logger = logging.getLogger(__name__)

ROOT_SPAN_NAME = "Alert Verification"

# Historical child spans, in pipeline order. Each entry is
# (span name, start-timestamp key, end-timestamp key).
_TIMESTAMP_STAGES: Tuple[Tuple[str, str, str], ...] = (
    ("Kafka Consume Lag", "kafkaPublishedAt", "kafkaConsumedAt"),
    ("Worker Queue Wait", "kafkaConsumedAt", "workerAssignedAt"),
    # event_loop only: the sync path never stamps these two keys.
    ("Dispatch Wait", "taskDispatchedAt", "taskStartedAt"),
)

# Duration-only stages: `latency` records {success, duration} with no absolute
# timestamp, so their start is reconstructed by walking forward from the last
# known absolute mark. Both VST keys can be populated for one event — the
# no-overlay entry is the retry fallback, not a replacement.
_DURATION_STAGES: Tuple[Tuple[str, str], ...] = (
    ("VST Video URL Resolution (overlay)", "getVideoStreamUrlWithOverlay"),
    ("VST Video URL Resolution (no overlay)", "getVideoStreamUrlWithoutOverlay"),
)

# `latency['timestamps']` accepts both spellings: camelCase is current, snake_case
# survives in older documents and in-flight events during a rolling deploy.
_SNAKE = {
    "kafkaPublishedAt": "kafka_published_at",
    "kafkaConsumedAt": "kafka_consumed_at",
    "workerAssignedAt": "worker_assigned_at",
    "taskDispatchedAt": "task_dispatched_at",
    "taskStartedAt": "task_started_at",
}


def _tracing_active() -> bool:
    """True when tracing is initialised **and** enabled in this process.

    The gate REQ-019 promises. ``_otel()`` alone is not it: it reports whether
    the package imports, and ``requirements.txt`` installs the package
    unconditionally, so without this every span site stayed live on the shipped
    default. Measured before this existed: 9 spans and 9 context attach/detach
    per alert with ``ENABLE_OTEL_MONITORING`` unset.

    Initialises this process on the way through - see ``ensure_initialised`` for
    why that belongs here rather than at an entry point.

    ``ensure_initialised`` is imported at module scope rather than per call: this
    runs at every span site on the shipped default, and re-resolving it each time
    cost more than the check it guards. There is no cycle - the package
    ``__init__`` does not import this module.
    """
    try:
        return ensure_initialised()
    except Exception:
        return False


def _otel():
    """Lazily import the tracing API; ``None`` when OTel is not installed."""
    try:
        from opentelemetry import context as otel_context
        from opentelemetry import trace

        return trace, otel_context
    except Exception:  # pragma: no cover - covered by test_everything_degrades_when_opentelemetry_is_absent
        return None


def _non_negative(value: float) -> Optional[float]:
    """Reject pre-epoch instants.

    Nothing in this pipeline legitimately predates 1970, so a negative value is a
    malformed timestamp rather than a real one. Passing it through produced a
    historical child that renders as starting decades before its parent, which is
    worse than the missing child that dropping it produces - a reader can see an
    absent span, but a 1969 one just looks like the trace is broken.
    """
    if value < 0 or value != value:  # NaN compares unequal to itself
        return None
    return value


def earliest_stamp(timestamps: Optional[Mapping[str, Any]]) -> Optional[float]:
    """When the event entered the pipeline, as an epoch, or ``None``.

    First valid stamp in pipeline order rather than the numeric minimum: order
    is the semantic answer to "when did this event arrive", and a single
    corrupted stamp should not be able to drag the root span's start backwards.

    Future stamps are refused - a clock skew ahead of this host would otherwise
    start the root after its own children.
    """
    if not timestamps:
        return None
    now = time.time()
    for _, start_key, _ in _TIMESTAMP_STAGES:
        # Through _timestamp, not timestamps.get: the snake_case fallback is the
        # spelling the sync path uses, and reading the dict directly meant a
        # snake_case event got no start_time and its three pre-entry children
        # rendered left of their own parent again.
        value = _timestamp(timestamps, start_key)
        if value is not None and value <= now:
            return value
    return None


def _epoch_seconds(raw: Any) -> Optional[float]:
    """Parse an ISO-8601 stamp to epoch seconds, or ``None`` if unusable.

    `latency` carries ISO strings while the SDK wants epoch nanoseconds, so every
    historical span goes through here. Anything unparseable yields ``None`` and
    the caller skips that span rather than guessing — a missing stage is a gap in
    the trace, a fabricated one is a lie.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        # bool is an int subclass; True would otherwise become 1970-01-01.
        return None
    if isinstance(raw, (int, float)):
        return _non_negative(float(raw))
    try:
        text = str(raw).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return _non_negative(parsed.timestamp())
    except Exception:
        return None


def _timestamp(timestamps: Mapping[str, Any], key: str) -> Optional[float]:
    """Read a timestamp by camelCase key, falling back to the snake_case spelling."""
    value = timestamps.get(key)
    if value is None:
        value = timestamps.get(_SNAKE.get(key, ""))
    return _epoch_seconds(value)


def _historical_span(tracer, parent_context, name: str, start: float, end: float,
                     attributes: Optional[Dict[str, Any]] = None) -> None:
    """Emit one already-finished child span with explicit start/end times."""
    if end < start:
        # A negative interval means the two stamps disagree; skipping is the same
        # tolerance `observe_pipeline_latency()` already applies to its histograms.
        return
    span = tracer.start_span(
        name,
        context=parent_context,
        start_time=int(start * 1_000_000_000),
        attributes=attributes or {},
    )
    span.end(end_time=int(end * 1_000_000_000))


def build_historical_children(span, latency: Optional[Mapping[str, Any]], tracer=None) -> None:
    """Reconstruct the per-stage child spans from the ``latency`` dict (REQ-002).

    Only stages the pipeline genuinely records are built. Notably absent:

    * ``elasticReadyAt`` — never written back into ``latency``; the recorder
      computes it into a throwaway local so a failure path cannot inject a
      synthetic timestamp into the Elasticsearch document.
    * ``stream_existence_validation`` — dead code, no production writer.
    * ``vlmRequest`` and ``capacityWait`` — these are emitted as **live** spans at
      their real call sites, because the dict keeps only the last VLM attempt and
      sums the two disjoint VST capacity waits into one scalar.
    """
    mod = _otel()
    if mod is None or span is None or not latency:
        return
    if not span.is_recording():
        # Sampling decides at start_span, so on an unsampled root every child
        # built below is constructed and immediately discarded. The SDK's no-op
        # set_attribute is not enough on its own: the attribute dict and the
        # timestamp parsing happen in Python before any setter is reached.
        # Measured at 23 us per event, on the 90% of events a 0.1 ratio drops.
        return
    trace, _ = mod
    try:
        if tracer is None:
            tracer = trace.get_tracer(__name__)
        parent_context = trace.set_span_in_context(span)
        timestamps = latency.get("timestamps") or {}

        last_absolute: Optional[float] = None
        for name, start_key, end_key in _TIMESTAMP_STAGES:
            start = _timestamp(timestamps, start_key)
            end = _timestamp(timestamps, end_key)
            if start is None or end is None:
                continue
            _historical_span(tracer, parent_context, name, start, end)
            last_absolute = max(last_absolute or end, end)

        # Duration-only stages run strictly in sequence after the last absolute
        # mark, so their start times are accumulated forward. This is a
        # reconstruction, not an independent measurement — a future contributor
        # should not mistake these boundaries for wall-clock marks.
        cursor = last_absolute
        for name, key in _DURATION_STAGES:
            entry = latency.get(key)
            if not isinstance(entry, Mapping):
                continue
            duration = entry.get("duration")
            if duration is None or cursor is None:
                continue
            try:
                duration = float(duration)
            except (TypeError, ValueError):
                continue
            if duration < 0:
                continue
            _historical_span(
                tracer,
                parent_context,
                name,
                cursor,
                cursor + duration,
                {"success": bool(entry.get("success")), "timing.source": "reconstructed"},
            )
            cursor += duration
    except Exception:
        # A bug in the builder must never reach the pipeline (REQ-019).
        logger.debug("building historical child spans failed", exc_info=True)


class RootSpanHandle:
    """Explicit handle for the root span — never looked up from ambient context.

    The deferred sink callback runs on an executor thread after the pipeline
    frame has returned, where ``trace.get_current_span()`` yields an invalid
    span. Passing the handle explicitly is what makes closure correct on that
    path; the attached context is a *complement* to the handle, for
    auto-instrumented children and log correlation, not a replacement for it.
    """

    __slots__ = (
        "_span",
        "_context_token",
        "_lock",
        "_deferred",
        "_decorated",
        "_finalized",
        "_finally_reached",
        "_closed",
        "_tracer",
    )

    def __init__(self, span, context_token=None, tracer=None):
        # Re-entrant: close() holds the lock across its own decorate() fallback.
        self._lock = threading.RLock()
        self._span = span
        self._context_token = context_token
        # Children are emitted from the same tracer that produced the root, so a
        # caller with its own provider (tests, or a second provider in-process)
        # gets a coherent tree instead of children on the global provider.
        self._tracer = tracer
        self._deferred = False
        self._decorated = False
        self._finalized = False
        self._finally_reached = False
        self._closed = False

    # -- state, all read and written under the lock -------------------------

    @property
    def span(self):
        return self._span

    @property
    def finalized(self) -> bool:
        with self._lock:
            return self._finalized

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def mark_finalized(self) -> None:
        """Record that the sink completion callback has run at least once."""
        with self._lock:
            self._finalized = True

    def mark_finally_reached(self) -> None:
        """Called as the FIRST statement of the outer ``finally``.

        It must precede the close decision so a callback firing afterwards can
        tell that the ``finally`` has already had its turn.
        """
        with self._lock:
            self._finally_reached = True

    def mark_deferred(self) -> bool:
        """Hand closure to the sink callback. Refuses if the callback already ran.

        Conditional rather than a plain setter on purpose: a separate
        ``if not finalized`` check followed by a set leaves a window for a
        sink-thread completion to slip between the two, after which the
        ``finally`` would skip a span nobody else closes.
        """
        with self._lock:
            if self._finalized:
                return False
            self._deferred = True
            return True

    def should_close_from_finally(self) -> bool:
        """The outer ``finally``'s single locked decision."""
        with self._lock:
            return (not self._deferred) or self._finalized

    def should_close_from_callback(self) -> bool:
        """The deferred callback's single locked decision."""
        with self._lock:
            return self._finally_reached

    # -- lifecycle ----------------------------------------------------------

    def is_recording(self) -> bool:
        """Whether this root was sampled. False once closed, or on a bad handle."""
        try:
            return bool(self._span is not None and self._span.is_recording())
        except Exception:
            return False

    def decorate(
        self,
        latency: Optional[Mapping[str, Any]] = None,
        message: Optional[Mapping[str, Any]] = None,
        failure_reason: Optional[str] = None,
    ) -> None:
        """Attach outcome attributes and the historical children. Never ends.

        Guards on ``_decorated`` **only**, never on ``_closed``: ``_closed`` means
        a closer has claimed the handle, not that the span has ended, and
        ``close()`` invokes this as its fallback while the span is still open.

        On an unsampled span this returns after claiming the flag but before
        doing any work: every setter downstream is a no-op, so the only thing
        building the attributes would achieve is the cost of building them.
        """
        with self._lock:
            if self._decorated:
                return
            self._decorated = True
        try:
            if self._span is None or not self.is_recording():
                # Nothing downstream records on an unsampled span, so verdict
                # lookup and child reconstruction are pure waste. The flag is
                # still set above: decoration has had its turn either way, and a
                # second caller must not retry it.
                return
            if latency:
                build_historical_children(self._span, latency, self._tracer)
            verdict = verdict_of(message)
            if verdict is not None:
                # Capped for the same reason attributes._put caps the
                # identifiers: info.verdict is producer-controlled free text
                # off a Kafka message that never passed through
                # AlertRequestEntity, so without this the two largest such
                # fields sat uncapped on the root while the smaller ones
                # were guarded.
                self._span.set_attribute(
                    "verdict", truncate(str(verdict), MAX_IDENTIFIER_CHARS))
            if failure_reason:
                self._span.set_attribute(
                    "error_reason",
                    truncate(str(failure_reason), MAX_IDENTIFIER_CHARS))
        except Exception:
            logger.debug("RootSpanHandle.decorate() failed; continuing", exc_info=True)

    def close(
        self,
        latency: Optional[Mapping[str, Any]] = None,
        message: Optional[Mapping[str, Any]] = None,
        failure_reason: Optional[str] = None,
    ) -> None:
        """End the span exactly once, decorating first if the recorder never ran."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if not self._decorated:
                    # `message` may be None on the deferred and on-demand paths.
                    # Guarding on it here meant close(latency, None,
                    # failure_reason=...) recorded neither the reason nor the
                    # historical children — it dropped the feature's main
                    # deliverable on exactly the paths that arrive next.
                    # decorate() already tolerates message=None via verdict_of().
                    self.decorate(latency, message, failure_reason)
                elif failure_reason and self._span is not None:
                    self._span.set_attribute(
                        "error_reason",
                        truncate(str(failure_reason), MAX_IDENTIFIER_CHARS))
            except Exception:
                logger.debug("RootSpanHandle.close() decoration failed", exc_info=True)
            finally:
                # Unconditional: a decoration bug must never orphan the span or
                # replace the caller's real exception.
                try:
                    if self._span is not None:
                        self._span.end()
                except Exception:
                    logger.debug("RootSpanHandle.close() end() failed", exc_info=True)

    def detach(self) -> None:
        """Detach the context token — once, and only on the originating thread.

        The deferred callback never attached anything of its own, so it must not
        call this.
        """
        with self._lock:
            token, self._context_token = self._context_token, None
        if token is None:
            return
        mod = _otel()
        if mod is None:
            return
        _, otel_context = mod
        try:
            otel_context.detach(token)
        except Exception:
            logger.debug("RootSpanHandle.detach() failed", exc_info=True)


@contextmanager
def live_span(name: str, **attributes: Any):
    """A real-time child of whatever span is current. No-op when tracing is off.

    Used where a historical reconstruction would be wrong rather than merely
    less precise: the VLM retry loop (``latency`` keeps only the last attempt)
    and the capacity slots (``_capacity_slot`` sums two disjoint VST waits into
    one scalar). Both need one span per occurrence, at the real time it happened.

    Records the exception type on failure but never swallows it — the caller's
    control flow is untouched, which is the whole contract tracing operates
    under here.
    """
    if not _tracing_active():
        yield None
        return
    mod = _otel()
    if mod is None:
        yield None
        return
    trace, _ = mod
    # Nothing below may ``yield`` inside a ``try`` that catches: contextlib
    # re-throws the caller's exception AT the suspended yield, so a yield inside
    # ``except Exception`` swallows the caller's error, yields a second time, and
    # the caller receives ``RuntimeError: generator didn't stop after throw()``
    # instead of its own exception. That is how the unsampled-parent guard below
    # first shipped, and on a 0.1 sampling ratio it rewrote the exception on 90%
    # of events - VLM timeouts stopped being retried and were published with the
    # wrong response code. The decision is made here; the yields happen after.
    span = None
    try:
        tracer = trace.get_tracer(__name__)
        parent = trace.get_current_span()
        if parent is None or not parent.get_span_context().is_valid \
                or parent.is_recording():
            include_content, max_content_chars = content_policy()
            span = tracer.start_span(
                name,
                attributes=manual_attributes(
                    None,
                    include_content=include_content,
                    max_content_chars=max_content_chars,
                    **attributes,
                ),
            )
        # else: the sampler already said no for this trace, so a child would be
        # non-recording too. Skip before building attributes rather than after.
    except Exception:
        logger.debug("live_span(%s) could not start", name, exc_info=True)
        span = None

    if span is None:
        yield None
        return

    try:
        with trace.use_span(span, end_on_exit=True, record_exception=False):
            try:
                yield span
            except Exception as exc:
                # Type name only: an exception message can carry a VST response
                # body, and this is a manual span so the exporter's redaction is
                # a backstop, not the first line of defence (REQ-014).
                try:
                    span.set_attribute("exception.type", type(exc).__name__)
                except Exception:
                    pass
                raise
    except Exception:
        raise


def traced_io(name: str, **static_attributes: Any):
    """Decorate a sync or async I/O method with a live span.

    For **local latency visibility only** — these spans say how long AB spent in
    a backend call, not what the backend did with it. Elasticsearch in
    particular gets no outbound ``traceparent``: that is a settled Non-Goal, not
    a deferral, so the span ends at AB's boundary by design.

    No payload, statement or document body is recorded. The gated builder in
    ``attributes.py`` governs what a manual span may carry, and an ES document is
    exactly the shape of thing that guideline exists to keep out of telemetry.
    """
    def decorator(func):
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                with live_span(name, **static_attributes):
                    return await func(*args, **kwargs)
            return async_wrapper

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with live_span(name, **static_attributes):
                return func(*args, **kwargs)
        return wrapper
    return decorator


def open_root_span(
    message: Optional[Mapping[str, Any]] = None,
    parent_context: Any = None,
    *,
    pipeline_mode: Optional[str] = None,
    timestamps: Optional[Mapping[str, Any]] = None,
    link_to: Any = None,
    payload_size_bytes: Optional[int] = None,
    include_content: Optional[bool] = None,
) -> Optional[RootSpanHandle]:
    """Open the root span and make it current. **Never raises.**

    Returns ``None`` on any failure, and on ``None`` every downstream site is a
    no-op — that is deliberate. This runs *before* the pipeline's outer ``try``,
    so an exception here would escape and kill the event, which is precisely what
    REQ-019 promises tracing cannot do.
    """
    try:
        if not _tracing_active():
            return None
        mod = _otel()
        if mod is None:
            return None
        trace, otel_context = mod
        # None means "whatever config resolved for this process" — the caller in
        # the pipeline passes nothing, which is how the configured policy reaches
        # the root span at all. Hardcoding False here meant
        # alert_agent.tracing.include_content could never take effect.
        policy_content, max_content_chars = content_policy()
        if include_content is None:
            include_content = policy_content
        attributes = manual_attributes(
            message,
            include_content=include_content,
            max_content_chars=max_content_chars,
            pipeline_mode=pipeline_mode,
            payload_size_bytes=payload_size_bytes,
        )
        tracer = trace.get_tracer(__name__)
        # Start the root when the event entered the pipeline, not when this
        # coroutine picked it up. Three of the five historical children --
        # Kafka Consume Lag, Worker Queue Wait and Dispatch Wait -- describe
        # stages that finished before that point, so a root started at function
        # entry ends up shorter than the stages it contains, and Jaeger renders
        # those children to the left of their own parent. The root's bar is what
        # an operator reads as "how long did this alert take"; it has to cover
        # the stages it claims to.
        start_time = earliest_stamp(timestamps)
        links = None
        if link_to is not None:
            # A Link *instead of* a parent, which means detaching from whatever
            # context is ambient here.
            #
            # The premise for this used to be "the scheduling span has already
            # ended, so there is nothing to inherit". That is wrong, and driving
            # it showed so: Starlette copies the request's contextvars into the
            # worker thread, so a background task runs with the request's context
            # fully live and this span came out as a *child*, two levels deep,
            # with its Link pointing at its own grandparent.
            #
            # Detaching is still right, and for the reason the link was for: the
            # auto-instrumented BackgroundTask span it would otherwise hang under
            # begins after its own parent ended, so inheriting means parenting
            # onto a span-lifetime violation. A root plus a link says what is
            # true -- separate unit of work, and here is what asked for it.
            from opentelemetry.context import Context
            from opentelemetry.trace import Link

            links = [Link(link_to)]
            if parent_context is None:
                parent_context = Context()
        span = tracer.start_span(
            ROOT_SPAN_NAME,
            context=parent_context,
            attributes=attributes,
            links=links,
            start_time=int(start_time * 1_000_000_000) if start_time is not None else None,
        )
        token = otel_context.attach(trace.set_span_in_context(span))
        return RootSpanHandle(span, context_token=token, tracer=tracer)
    except Exception:
        logger.warning("open_root_span() failed; continuing untraced", exc_info=True)
        return None

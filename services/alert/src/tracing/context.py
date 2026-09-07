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

"""W3C trace-context propagation in and out of Alert Bridge (REQ-007, REQ-012).

Three directions, all of them optional and all of them silent on failure:

* **In, from Kafka** — a producer's ``traceparent`` header. **Wired** (REQ-007).
  It used to be dropped twice: the broker returned ``(key, value, timestamp)``
  and the protobuf decoder collapsed those tuples into bare JSON strings, losing
  which record produced which string. The broker now returns a four-field
  ``KafkaMessage`` and the decoder returns ``(json_str, source_record)`` pairs,
  so ``open_root_span()`` can parent each record on its own inbound context
  rather than on whatever the batch happened to start with. No VSS producer
  sends the header yet, so today this parents nothing -- but the receiving half
  is the one that needs no coordination, and a mixed-parent batch now works the
  moment a sender exists.
* **Out** — ``traceparent`` into an outgoing carrier. Wired on **one** produce
  site, ``mdx/sink/vlm_enhanced_sink/sink_kafka.py``: the others
  (``mdx/sink/sink_kafka.py``, ``web/core/alert_service.py``) pass no ``headers=``
  and so emit none. Worth knowing because ``alert_service`` produces to the very
  topics this pipeline consumes, which makes REST → Kafka → pipeline the one
  complete REQ-007 chain that lives entirely inside this repository -- one
  ``headers=`` argument away from working.

Inbound HTTP is deliberately absent. An earlier revision had a helper for it;
the FastAPI instrumentation runs the global propagator on the request itself, so
the helper was never called from anything but its own test, and a second
mechanism sitting next to the real one is an invitation to wire up the wrong one.

Every function returns ``None``/leaves the carrier untouched when tracing is
disabled or the SDK is absent. Nothing here may raise into the pipeline: a
propagation failure must degrade to an unparented span, never to a dropped
alert (REQ-019).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple, Union

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """Whether tracing is active here; imported lazily to avoid a cycle."""
    try:
        from tracing import is_enabled as _is_enabled

        return _is_enabled()
    except Exception:
        return False

# Kafka headers arrive from confluent-kafka as a list of (str, bytes) pairs, or
# None when the record carried none.
KafkaHeaders = Optional[Iterable[Tuple[str, Union[bytes, str, None]]]]


def _propagators():
    """Import the propagation API lazily; ``None`` when OTel is not installed."""
    try:
        from opentelemetry import trace
        from opentelemetry.propagate import extract, inject

        return trace, extract, inject
    except Exception:  # pragma: no cover - covered by test_everything_degrades_when_opentelemetry_is_absent
        return None


def _extract_from_carrier(carrier: Mapping[str, str]) -> Optional[Any]:
    """Extract a remote context from an already-lowercased carrier.

    ``extract()`` always returns a Context. Without a usable ``traceparent`` it
    holds an invalid span, which would silently mint a *fresh* trace id and look
    like propagation worked — so validity is checked before returning.
    """
    mod = _propagators()
    if mod is None:
        return None
    trace, extract, _ = mod
    try:
        ctx = extract(dict(carrier))
        span_context = trace.get_current_span(ctx).get_span_context()
        if not span_context.is_valid:
            return None
        return ctx
    except Exception:
        logger.debug("trace-context extraction failed", exc_info=True)
        return None


def extract_context_from_kafka_headers(headers: KafkaHeaders) -> Optional[Any]:
    """Build a parent context from Kafka record headers (REQ-007).

    Called from :func:`tracing.spans.open_root_span` when the pipeline hands it
    a record's header block; see ``KAFKA_HEADERS_KEY`` for how the association
    survives decode and filtering.

    Tolerates ``None``, byte values, non-UTF8 values and duplicate keys — a
    malformed header is upstream's problem and must not cost an alert. A
    duplicate ``traceparent`` is the one case that costs the parent: it is
    ambiguous, so the record starts a fresh trace rather than being attributed
    to one of the two on a coin flip (TS-027). Duplicates of any other name
    keep their first occurrence.
    """
    if not headers:
        return None
    carrier: Dict[str, str] = {}
    seen = set()
    try:
        for key, value in headers:
            if key is None:
                continue
            name = str(key).lower()
            # Count the occurrence before judging the value. Keying the
            # duplicate check off `carrier` instead would only see occurrences
            # that decoded cleanly, so a first `traceparent` that is non-UTF8
            # or None -- TS-027's own sub-cases -- would leave the second one
            # looking unique and get the record parented anyway.
            if name in seen:
                if name == "traceparent":
                    logger.debug(
                        "duplicate traceparent header on a record; starting a "
                        "fresh trace rather than guessing which one is real"
                    )
                    return None
                continue
            seen.add(name)
            if isinstance(value, bytes):
                try:
                    value = value.decode("utf-8")
                except UnicodeDecodeError:
                    continue
            if value is None:
                continue
            carrier[name] = str(value)
    except Exception:
        logger.debug("could not read Kafka headers for trace context", exc_info=True)
        return None
    return _extract_from_carrier(carrier)


def inject_traceparent(carrier: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Return ``carrier`` with the current context's ``traceparent`` added (REQ-009).

    Mutates and returns the mapping so it can be used inline as request headers.
    With tracing off, or no active span, the carrier comes back unchanged — which
    is the correct outbound behaviour, not an error.
    """
    if carrier is None:
        carrier = {}
    mod = _propagators()
    if mod is None:
        return carrier
    _, _, inject = mod
    try:
        inject(carrier)
    except Exception:
        logger.debug("trace-context injection failed", exc_info=True)
    return carrier


def kafka_headers_for_current_span() -> Optional[list]:
    """``traceparent`` as confluent-kafka headers, or ``None``.

    ``None`` rather than an empty list when there is nothing to send, so a caller
    can pass it straight to ``produce(headers=...)`` and get today's behaviour
    exactly -- confluent-kafka treats None as "no headers", which is what an
    untraced produce already sends.

    This is the sending half of REQ-007. Nothing in VSS currently *reads* a
    traceparent off a Kafka record. AB's own consumer does now (REQ-007), but it
    is the only one. It can read its own produces -- ``alert_service`` writes to
    the topics the pipeline consumes -- so this is not purely for other services.
    Emitting it anyway is what
    makes the header exist at all, and it costs one small string per record.
    """
    carrier = inject_traceparent({})
    if not carrier:
        return None
    return [(k, v.encode("utf-8") if isinstance(v, str) else v) for k, v in carrier.items()]


def current_span_context() -> Optional[Any]:
    """The current span's ``SpanContext``, or ``None``.

    For work that outlives the span that scheduled it. A FastAPI background task
    runs *after* the response is sent and after the server span has ended, so it
    cannot be that span's child: by the time it starts, the parent is over. What
    it can carry is a Link to where it came from, and this is what captures that
    at the moment the task is queued.
    """
    mod = _propagators()
    if mod is None:
        return None
    trace, _, _ = mod
    try:
        span_context = trace.get_current_span().get_span_context()
        return span_context if span_context.is_valid else None
    except Exception:
        return None


def current_trace_ids() -> Tuple[Optional[str], Optional[str]]:
    """``(trace_id, span_id)`` as zero-padded hex for log correlation (REQ-012).

    ``(None, None)`` when tracing is off or no span is current — with no context
    attached, ``get_current_span()`` returns a non-recording span whose context
    is invalid, which is exactly the signal needed to make log correlation a
    no-op without the logging layer having to know whether tracing is enabled.
    """
    # Short-circuit before touching the SDK. This runs on *every* log record --
    # the filter is installed unconditionally so correlation survives the two
    # fallback branches -- and measured at +55% per record when tracing is off,
    # against a module docstring that promises no latency added in that case.
    if not is_enabled():
        return (None, None)
    mod = _propagators()
    if mod is None:
        return (None, None)
    trace, _, _ = mod
    try:
        span_context = trace.get_current_span().get_span_context()
        if not span_context.is_valid:
            return (None, None)
        return (
            format(span_context.trace_id, "032x"),
            format(span_context.span_id, "016x"),
        )
    except Exception:
        return (None, None)

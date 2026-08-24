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

"""Module-level Prometheus recording helpers for the VLM verification pipeline.

This module owns all calls into the prometheus_client API so the rest of the
codebase does not have to know whether metrics are enabled. The functions are
pure module-level callables (no class, no ``self``) so they:

  * do not couple metric recording to ``AnomalyEnhancer`` construction;
  * can be reused from the realtime / REST surfaces without instantiating the
    orchestrator;
  * let unit tests import and exercise them directly without standing up the
    full pipeline.

When ``PROMETHEUS_METRICS_ENABLED`` is false every public entry point is a
cheap no-op (one flag check + early return), so callers do not need their
own ``if PROMETHEUS_ENABLED:`` guards.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from metrics import PROMETHEUS_ENABLED
from utils.time_utils import iso_delta_seconds

logger = logging.getLogger(__name__)

# Metric objects live inside ``prometheus_client``; importing the module is a
# no-op when the feature is disabled, so we only pull the symbols when they
# will actually be referenced. Centralizing the conditional import here (and
# keeping the call sites in ``enhance_alert_with_vlm.py`` unaware of metric
# objects altogether) removes the ``NameError`` foot-gun that used to exist
# when metric names were dereferenced from class methods while their imports
# sat inside an unrelated ``if PROMETHEUS_ENABLED:`` block at module top.
if PROMETHEUS_ENABLED:
    from metrics.prometheus_metrics import (
        ASYNC_EXTERNAL_IO_FALLBACK_TOTAL,
        E2E_DURATION,
        E2E_DURATION_BY_SENSOR,
        EVENTS_AFTER_DEDUP,
        EVENTS_AFTER_DEDUP_BY_SENSOR,
        EVENTS_DROPPED,
        EVENTS_DROPPED_BY_SENSOR,
        EVENTS_SKIPPED_CONFIRMED,
        EVENTS_SKIPPED_CONFIRMED_BY_SENSOR,
        EVENTS_TOTAL,
        EVENTS_TOTAL_BY_SENSOR,
        KAFKA_LAG_DURATION,
        KAFKA_LAG_DURATION_BY_SENSOR,
        UPSTREAM_DURATION,
        UPSTREAM_DURATION_BY_SENSOR,
        VERIFICATION_FAILURES,
        VERIFICATION_FAILURES_BY_SENSOR,
        VIDEO_LENGTH,
        VIDEO_LENGTH_BY_SENSOR,
        VLM_DURATION,
        VLM_DURATION_BY_SENSOR,
        VST_DURATION,
        VST_DURATION_BY_SENSOR,
        WORKER_PROCESSING_DURATION,
        WORKER_PROCESSING_DURATION_BY_SENSOR,
        WORKER_QUEUE_WAIT_DURATION,
        WORKER_QUEUE_WAIT_DURATION_BY_SENSOR,
    )


# ── Per-sensor label gate (C21) ───────────────────────────────────────────
# Opt-in, set at startup by ``AnomalyEnhancer.__init__`` based on the
# ``alert_agent.metrics.per_sensor_labels`` config key. When off the
# ``*_BY_SENSOR`` counters stay absent from the scrape (zero
# cardinality cost); when on, every event-accounting counter above
# also increments its per-sensor variant.
_per_sensor_labels_enabled = False


def set_per_sensor_labels(enabled: bool) -> None:
    """Toggle the per-sensor metric variants.

    Called once from ``AnomalyEnhancer.__init__`` after config load
    and before any event processing starts. We keep the flag in a
    process-global rather than re-reading config on every event —
    the per-event check has to be single-instruction.
    """
    global _per_sensor_labels_enabled
    _per_sensor_labels_enabled = bool(enabled)


def per_sensor_labels_enabled() -> bool:
    """Introspection helper for tests + the latency report script."""
    return _per_sensor_labels_enabled


# Max accepted length for a sanitized sensorId label. Pathological
# upstream producers could, in principle, write 10KB of free-form
# text into ``sensorId`` — and because Prometheus labels are permanent
# for the life of the scrape target, even one such value is a cardinality
# time-bomb. 128 chars comfortably covers every real-world sensor
# identifier format (UUIDs, path-style IDs, camera-name-plus-location).
_SENSOR_ID_MAX_LEN = 128

# Hard cap on the number of distinct sensorId label values this process
# will ever mint. Each new distinct ID adds ~80 Prometheus series
# (8 histograms × ~10 buckets each); at the default cap of 128 sensors
# the per-sensor surface is ~10k series — right at the guideline budget.
# Once the cap is reached every new unseen ID folds to "unknown_overflow"
# so excess traffic remains visible without growing the registry further.
# Prometheus does not garbage-collect label values, so recovery from a
# runaway fleet requires a process restart — the cap makes that a
# deliberate choice rather than an accident.
_MAX_SENSOR_IDS = 128
_KNOWN_SENSOR_IDS: set = set()
# Protects the check-then-add sequence in _sanitize_sensor_id so the cap
# cannot be violated when worker threads call it concurrently.
_KNOWN_SENSOR_IDS_LOCK = threading.Lock()


def _sanitize_sensor_id(raw) -> str:
    """Normalize an incoming ``sensorId`` to a bounded label value.

    Anything that isn't a non-empty string fitting inside the length
    budget folds to ``"unknown"``. Once ``_MAX_SENSOR_IDS`` distinct
    values have been seen, any further unseen ID folds to
    ``"unknown_overflow"`` and a one-time WARNING is emitted so the
    operator knows the cap was hit.
    """
    if not isinstance(raw, str):
        return "unknown"
    stripped = raw.strip()
    if not stripped or len(stripped) > _SENSOR_ID_MAX_LEN:
        return "unknown"

    # Fast path — no lock needed. ``set.__contains__`` is a single atomic
    # bytecode under the GIL, and already-known IDs are the common case.
    if stripped in _KNOWN_SENSOR_IDS:
        return stripped

    # Slow path — potential new ID. Acquire the lock so the cap check and
    # the add are atomic with respect to other threads.
    with _KNOWN_SENSOR_IDS_LOCK:
        # Re-check under the lock: another thread may have added this ID
        # between the unlocked membership test above and here.
        if stripped in _KNOWN_SENSOR_IDS:
            return stripped
        if len(_KNOWN_SENSOR_IDS) >= _MAX_SENSOR_IDS:
            logger.warning(
                "_sanitize_sensor_id: distinct sensorId cap (%d) reached; "
                "%r will be recorded as 'unknown_overflow'. To raise the cap "
                "increase _MAX_SENSOR_IDS in metrics/recorder.py.",
                _MAX_SENSOR_IDS,
                stripped,
            )
            return "unknown_overflow"
        _KNOWN_SENSOR_IDS.add(stripped)
    return stripped

# Allowlist of valid ``reason`` labels for ``EVENTS_DROPPED``. Keeping this
# here (rather than deriving it from the call sites) lets us eagerly warm
# every label combination at startup so ``rate()`` queries behave during
# the first scrape window — see C15 follow-up.
EVENTS_DROPPED_REASONS = ("end_time_delta", "dedup", "rate_limit")

# Allowlist of valid ``verdict`` labels for ``EVENTS_TOTAL``.
#
# Without this, any upstream producer writing a free-form string into
# ``info.verdict`` would mint a brand-new Prometheus time series — and
# Prometheus label cardinality is permanent for the life of the scrape
# target. ``verification-failed`` already expanded the natural
# ``{confirmed, rejected, unknown}`` enum, so we codify the full set
# here and route anything else into ``unknown`` (with a once-per-value
# WARNING so drift stays discoverable in logs).
EVENTS_VERDICTS = ("confirmed", "rejected", "verification-failed", "unknown")

# Tracks verdict values we've already warned about, so a badly-behaving
# upstream producer cannot spam the log at every event. We intentionally
# keep this unbounded — the expected value count is O(typos in the
# codebase), and a pathological sensor flooding the set would itself be
# a useful signal for operators.
_UNKNOWN_VERDICTS_SEEN: set = set()


def _normalize_verdict(raw: Any) -> str:
    """Map an incoming ``info.verdict`` to the ``EVENTS_VERDICTS`` allowlist.

    Anything outside the allowlist (including ``None``, empty string,
    non-string types, and free-form values) collapses to ``"unknown"``.
    The first time each distinct unknown value is seen we emit one
    ``WARNING`` log so the drift is visible without log-flooding the
    pipeline on every event.
    """
    if isinstance(raw, str) and raw in EVENTS_VERDICTS:
        return raw
    # Stringify non-string inputs purely for log visibility — the metric
    # itself always receives ``"unknown"``.
    raw_repr = raw if isinstance(raw, str) else repr(raw)
    if raw_repr not in _UNKNOWN_VERDICTS_SEEN:
        _UNKNOWN_VERDICTS_SEEN.add(raw_repr)
        logger.warning(
            "EVENTS_TOTAL: unrecognized verdict %r — recording as 'unknown'. "
            "Expected one of %s.",
            raw_repr,
            EVENTS_VERDICTS,
        )
    return "unknown"


def _observe(metric: Any, value: Optional[float]) -> None:
    """Observe ``value`` on ``metric`` iff metrics are enabled and value is usable.

    Private helper that collapses the nine copies of
    ``if PROMETHEUS_ENABLED: METRIC.observe(duration)`` that used to live in
    ``enhance_alert_with_vlm.py``. Treats ``None`` as "nothing to observe"
    so callers do not have to pre-check ``iso_delta_seconds`` results.
    """
    if not PROMETHEUS_ENABLED or metric is None or value is None:
        return
    metric.observe(value)


def _observe_by_sensor(metric: Any, value: Optional[float], raw_sensor_id: Any) -> None:
    """Observe ``value`` on a ``sensorId`` labelled histogram when opted in."""
    if (
        not PROMETHEUS_ENABLED
        or not _per_sensor_labels_enabled
        or metric is None
        or value is None
    ):
        return
    metric.labels(sensorId=_sanitize_sensor_id(raw_sensor_id)).observe(value)


# ── Per-stage observation helpers ─────────────────────────────────────────
# Named helpers, one per histogram, keep the call sites self-documenting
# (``observe_vst_duration(duration)`` is clearer than
# ``_observe(VST_DURATION, duration)``) and mean the call sites never need
# to reference a metric object directly, so a future rename stays local.


def observe_vst_duration(duration: float, sensor_id: Any = None) -> None:
    """Record one VST fetch attempt duration."""
    _observe(VST_DURATION if PROMETHEUS_ENABLED else None, duration)
    _observe_by_sensor(
        VST_DURATION_BY_SENSOR if PROMETHEUS_ENABLED else None,
        duration,
        sensor_id,
    )


def observe_vlm_duration(duration: float, sensor_id: Any = None) -> None:
    """Record one VLM inference attempt duration."""
    _observe(VLM_DURATION if PROMETHEUS_ENABLED else None, duration)
    _observe_by_sensor(
        VLM_DURATION_BY_SENSOR if PROMETHEUS_ENABLED else None,
        duration,
        sensor_id,
    )


def observe_video_length(clip_seconds: Optional[float], sensor_id: Any = None) -> None:
    """Record the effective video-clip length returned by VST.

    Accepts ``None`` so callers can pass ``iso_delta_seconds(...)`` directly
    without gating on a successful parse.
    """
    _observe(VIDEO_LENGTH if PROMETHEUS_ENABLED else None, clip_seconds)
    _observe_by_sensor(
        VIDEO_LENGTH_BY_SENSOR if PROMETHEUS_ENABLED else None,
        clip_seconds,
        sensor_id,
    )


# ── Batch-level event-count helpers ───────────────────────────────────────
# These counters live outside the per-event hot path (they are incremented
# by batch counts, not per event). Routing them through helpers instead of
# touching the metric objects directly keeps the feature-flag gate in one
# place and makes the call sites in ``process_batch_vlm`` readable.


def inc_events_after_dedup(count: int, messages=None) -> None:
    """Record how many events survived all pre-processing filters.

    Call once per batch after the last filter (rate-limit) runs. ``count``
    is the number of messages that will actually be dispatched into
    ``_process_single_message``.

    When the C21 per-sensor opt-in is on, pass the ``messages`` iterable
    so we can emit the per-sensor breakdown in the same call. Omitting
    it when the flag is off avoids the tuple iteration cost on the hot
    path.
    """
    if not PROMETHEUS_ENABLED or count <= 0:
        return
    if _per_sensor_labels_enabled and count > 0 and messages is None:
        logger.warning(
            "inc_events_after_dedup: per_sensor_labels is enabled but messages=None; "
            "per-sensor counter will diverge from aggregate by %d.",
            count,
        )
    EVENTS_AFTER_DEDUP.inc(count)
    if _per_sensor_labels_enabled and messages:
        for sensor_id, n in _count_by_sensor(messages).items():
            EVENTS_AFTER_DEDUP_BY_SENSOR.labels(sensorId=sensor_id).inc(n)


def inc_events_dropped(reason: str, count: int, messages=None) -> None:
    """Record events dropped by a batch-level pre-processing filter.

    ``reason`` must be one of :data:`EVENTS_DROPPED_REASONS` — new reasons
    should be added to the allowlist rather than passed ad-hoc, so the
    dashboard author's ``sum by (reason) (...)`` query stays meaningful.
    A zero ``count`` is a no-op (common case: filter ran but dropped
    nothing).

    When the C21 per-sensor opt-in is on, pass the ``messages`` iterable
    (the actually-dropped ones, not the whole batch) so the per-sensor
    variant increments in lockstep.
    """
    if not PROMETHEUS_ENABLED or count <= 0:
        return
    if _per_sensor_labels_enabled and messages is None:
        logger.warning(
            "inc_events_dropped(%r): per_sensor_labels is enabled but messages=None; "
            "per-sensor counter will diverge from aggregate by %d.",
            reason,
            count,
        )
    # Defensive: drop unknown reasons to a single catch-all so we cannot
    # accidentally mint new Prometheus series from a typo at a call site.
    normalized = reason if reason in EVENTS_DROPPED_REASONS else "unknown"
    EVENTS_DROPPED.labels(reason=normalized).inc(count)
    if _per_sensor_labels_enabled and messages:
        for sensor_id, n in _count_by_sensor(messages).items():
            EVENTS_DROPPED_BY_SENSOR.labels(reason=normalized, sensorId=sensor_id).inc(n)


def _count_by_sensor(messages) -> Dict[str, int]:
    """Group an iterable of messages by sanitized ``sensorId``.

    Pulled out as a helper so the per-sensor batch counters stay a
    one-liner at the call site. We sanitize once per group rather than
    per event, which is also why we accumulate before calling
    ``.labels(...).inc(n)`` — it keeps the number of
    ``prometheus_client`` lock acquisitions minimal even when the
    batch is large.
    """
    counts: Dict[str, int] = {}
    for msg in messages:
        sensor_id = _sanitize_sensor_id(msg.get("sensorId") if isinstance(msg, dict) else None)
        counts[sensor_id] = counts.get(sensor_id, 0) + 1
    return counts


# ── Aggregate event helpers ───────────────────────────────────────────────


def observe_pipeline_latency(
    message: Dict[str, Any],
    latency: Dict[str, Any],
) -> None:
    """Observe every per-stage latency histogram derivable from ``latency``.

    Pulls the four pipeline timestamps out of ``latency['timestamps']`` and
    the event end timestamp off ``message``, then observes each histogram
    whose endpoints are available and yield a non-negative delta. Missing
    stages are silently skipped so a single bad timestamp never prevents the
    surrounding stages from being recorded.
    """
    if not PROMETHEUS_ENABLED:
        return

    timestamps = latency.get("timestamps") or {}
    # Accept both camelCase (current format) and snake_case (legacy format
    # present in older documents and in-flight events during a rolling deploy).
    kafka_pub     = timestamps.get("kafkaPublishedAt") or timestamps.get("kafka_published_at")
    kafka_con     = timestamps.get("kafkaConsumedAt")  or timestamps.get("kafka_consumed_at")
    worker_asgn   = timestamps.get("workerAssignedAt") or timestamps.get("worker_assigned_at")
    elastic_ready = timestamps.get("elasticReadyAt")   or timestamps.get("elastic_ready_at")
    event_end = message.get("end")
    sensor_id = message.get("sensorId")

    upstream = iso_delta_seconds(event_end, kafka_pub)
    kafka_lag = iso_delta_seconds(kafka_pub, kafka_con)
    queue_wait = iso_delta_seconds(kafka_con, worker_asgn)
    e2e = iso_delta_seconds(event_end, elastic_ready)

    _observe(UPSTREAM_DURATION, upstream)
    _observe_by_sensor(UPSTREAM_DURATION_BY_SENSOR, upstream, sensor_id)
    _observe(KAFKA_LAG_DURATION, kafka_lag)
    _observe_by_sensor(KAFKA_LAG_DURATION_BY_SENSOR, kafka_lag, sensor_id)
    _observe(WORKER_QUEUE_WAIT_DURATION, queue_wait)
    _observe_by_sensor(WORKER_QUEUE_WAIT_DURATION_BY_SENSOR, queue_wait, sensor_id)
    _observe(E2E_DURATION, e2e)
    _observe_by_sensor(E2E_DURATION_BY_SENSOR, e2e, sensor_id)


def record_event_complete(
    worker_start_time: float,
    message: Dict[str, Any],
    latency: Dict[str, Any],
    failure_reason: Optional[str] = None,
) -> None:
    """Record every per-event Prometheus metric at pipeline completion.

    Must be called at every exit point of ``_process_single_message`` so the
    pipeline latency, worker duration, verdict counter, and (when applicable)
    failure counter reflect every event — not just those that produce a
    successful ES write.

    This function does **not** mutate ``latency``. If ``elasticReadyAt`` is
    absent from the timestamps (early-exit failure paths), a local wall-clock
    value is used purely for the E2E histogram observation without being
    written back into the caller's dict — the ES payload is left unmodified.
    """
    if not PROMETHEUS_ENABLED:
        return

    # Compute elasticReadyAt locally for the Prometheus observation only.
    # We must NOT write back to latency: the same dict reference is later
    # serialised into the ES document, and injecting a synthetic timestamp
    # on failure paths would make ES documents misleading.
    existing_timestamps = latency.get("timestamps") or {}
    elastic_ready_at = (
        existing_timestamps.get("elasticReadyAt")
        or datetime.now(timezone.utc).isoformat()
    )
    latency_for_obs = {
        **latency,
        "timestamps": {**existing_timestamps, "elasticReadyAt": elastic_ready_at},
    }

    observe_pipeline_latency(message, latency_for_obs)
    worker_processing_duration = time.time() - worker_start_time
    _observe(WORKER_PROCESSING_DURATION, worker_processing_duration)
    _observe_by_sensor(
        WORKER_PROCESSING_DURATION_BY_SENSOR,
        worker_processing_duration,
        message.get("sensorId"),
    )

    verdict = _normalize_verdict((message.get("info") or {}).get("verdict"))
    EVENTS_TOTAL.labels(verdict=verdict).inc()
    if _per_sensor_labels_enabled:
        sensor_id = _sanitize_sensor_id(message.get("sensorId"))
        EVENTS_TOTAL_BY_SENSOR.labels(verdict=verdict, sensorId=sensor_id).inc()

    if failure_reason:
        VERIFICATION_FAILURES.labels(reason=failure_reason).inc()
        if _per_sensor_labels_enabled:
            sensor_id = _sanitize_sensor_id(message.get("sensorId"))
            VERIFICATION_FAILURES_BY_SENSOR.labels(
                reason=failure_reason, sensorId=sensor_id,
            ).inc()


def inc_async_dispatch_fallback(reason: str) -> None:
    """Record one async-dispatch-to-sync fallback (C26).

    The dispatch mixin has two code paths that silently fall back to
    inline (synchronous) processing when the async pipeline can't take
    the work:

      * ``executor_unavailable`` — ``self._message_dispatch_executor``
        is ``None`` (startup/shutdown race, or the executor was torn
        down). The message is processed inline and the batch thread
        continues.
      * ``submit_error`` — ``executor.submit(...)`` itself raised
        (thread-pool overflow, executor shutdown race, etc.). Same
        inline fallback.

    Reusing the existing ``ASYNC_EXTERNAL_IO_FALLBACK_TOTAL`` counter
    (rather than defining a third) is deliberate: operators already
    query
    ``rate(alert_bridge_async_external_io_fallback_total[5m])``
    for Redis / VST / Elastic fallbacks, and the ``operation`` label
    ``"dispatch_message"`` slots straight into the same PromQL
    without any dashboard changes. Reasons match the existing
    taxonomy (``submit_error`` already exists for the sink side).
    """
    if not PROMETHEUS_ENABLED:
        return
    ASYNC_EXTERNAL_IO_FALLBACK_TOTAL.labels(
        operation="dispatch_message",
        reason=reason,
    ).inc()


def inc_events_skipped_confirmed(message=None) -> None:
    """Record one event that short-circuited because a confirmed verdict
    already existed in Redis.

    Called from ``_set_message_id_and_should_skip`` before returning
    ``True``. This keeps the dedup-skip visible on dashboards without
    conflating it with ``EVENTS_TOTAL`` (those events never completed
    the pipeline and their verdict is not fresh). It is the counter
    that closes the second half of the C2 reconciliation invariant:

        EVENTS_AFTER_DEDUP = sum(EVENTS_TOTAL) + EVENTS_SKIPPED_CONFIRMED

    When the C21 per-sensor opt-in is on and ``message`` is supplied,
    the per-sensor variant increments in lockstep.
    """
    if not PROMETHEUS_ENABLED:
        return
    if _per_sensor_labels_enabled and message is None:
        logger.warning(
            "inc_events_skipped_confirmed: per_sensor_labels is enabled but message=None; "
            "per-sensor counter will diverge from aggregate."
        )
    EVENTS_SKIPPED_CONFIRMED.inc()
    if _per_sensor_labels_enabled and message is not None:
        sensor_id = _sanitize_sensor_id(
            message.get("sensorId") if isinstance(message, dict) else None
        )
        EVENTS_SKIPPED_CONFIRMED_BY_SENSOR.labels(sensorId=sensor_id).inc()


# ── Startup label warmup (C15) ────────────────────────────────────────────
#
# Prometheus scrapes landing between the HTTP-server bind and the first
# observation/increment see labelled counters as *absent* — not as zero.
# ``rate()`` and ``increase()`` treat absent series very differently from
# zero-valued series, so startup-window scrapes can produce spurious
# "metric disappeared" alerts until the first real event lands. Worse,
# some alerting rules (``absent_over_time``) would fire every time the
# process restarts.
#
# Eagerly calling ``.labels(value).inc(0)`` for every known label
# combination before the server binds turns "absent" into "present with
# value 0". Counters are monotonic so an ``inc(0)`` has no numerical
# effect on the cumulative value — it just materializes the series.

# Verdict labels matching the C8 allowlist. Kept in sync so any future
# edit to ``EVENTS_VERDICTS`` automatically warms the new labels too.
_WARMUP_VERDICTS = EVENTS_VERDICTS

# Reason labels for EVENTS_DROPPED (C22).
_WARMUP_DROP_REASONS = EVENTS_DROPPED_REASONS

# Reason labels for VERIFICATION_FAILURES. Sources:
#   VST taxonomy — see ``_classify_vst_failure_reason`` in
#   ``enhance_alert_with_vlm.py`` (C14).
#   VLM taxonomy — the four ``raise e`` branches in
#   ``_process_single_message`` plus the legacy url/parse/unknown values.
#   ``no_prompt`` — alert type has no prompt configured (C10).
#   ``redis_unavailable`` — Redis failure during the confirmed-verdict
#     skip check (C25); covered by
#     ``_classify_pre_processing_failure``.
_WARMUP_VERIFICATION_REASONS = (
    "vst_timeout", "vst_overloaded", "vst_not_found",
    "vst_unavailable", "vst_client_error", "vst_server_error",
    "vst_unknown",
    "url_validation",
    "vlm_parse_failure", "vlm_timeout", "vlm_connection_error",
    "vlm_server_error", "vlm_invalid_payload",
    "no_prompt",
    "redis_unavailable",
    "unknown",
)


def warm_startup_labels() -> None:
    """Materialize every known label combination at value 0.

    Call once, **after** ``AnomalyEnhancer`` finishes initializing and
    **before** ``start_http_server`` binds the Prometheus scrape port.
    This closes the window in which a scrape would see a half-populated
    registry.

    Unlabelled counters (``EVENTS_AFTER_DEDUP``, ``EVENTS_SKIPPED_CONFIRMED``)
    do not need this treatment — they materialize as soon as the
    Histogram/Counter is defined at module import time, which happens
    well before this call. The targets here are the labelled counters
    (``EVENTS_TOTAL``, ``EVENTS_DROPPED``, ``VERIFICATION_FAILURES``)
    whose children are constructed lazily by ``.labels(...)``.
    """
    if not PROMETHEUS_ENABLED:
        return

    for verdict in _WARMUP_VERDICTS:
        EVENTS_TOTAL.labels(verdict=verdict).inc(0)

    for reason in _WARMUP_DROP_REASONS:
        EVENTS_DROPPED.labels(reason=reason).inc(0)

    for reason in _WARMUP_VERIFICATION_REASONS:
        VERIFICATION_FAILURES.labels(reason=reason).inc(0)

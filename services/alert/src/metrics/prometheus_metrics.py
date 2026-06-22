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

"""Prometheus metric definitions for Alert Bridge pipeline monitoring."""

from prometheus_client import Counter, Gauge, Histogram

# ── Per-stage latency histograms ────────────────────────────────────────────
# These are recorded in-process so they capture the *actual* timing seen by
# Alert Agent, independent of what Elasticsearch eventually stores.

UPSTREAM_DURATION_BUCKETS = [0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 30.0, 60.0]
KAFKA_LAG_BUCKETS = [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
WORKER_QUEUE_WAIT_BUCKETS = [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
VST_DURATION_BUCKETS = [0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
VIDEO_LENGTH_BUCKETS = [1.0, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0, 30.0, 60.0]
VLM_DURATION_BUCKETS = [0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 30.0]
WORKER_PROCESSING_BUCKETS = [1.0, 2.5, 5.0, 10.0, 15.0, 30.0, 60.0]
E2E_DURATION_BUCKETS = [1.0, 2.5, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0]

# Upstream pipeline: time from event end timestamp to Kafka publish
# Covers CV + Analytics processing before Alert Agent receives the message.
UPSTREAM_DURATION = Histogram(
    'alert_bridge_upstream_duration_seconds',
    'Time from event end to Kafka publish (CV+Analytics pipeline latency)',
    buckets=UPSTREAM_DURATION_BUCKETS,
)

# Kafka consumer lag: time from Kafka publish to Alert Agent consumption
KAFKA_LAG_DURATION = Histogram(
    'alert_bridge_kafka_lag_duration_seconds',
    'Time from Kafka publish to Alert Agent consumption',
    buckets=KAFKA_LAG_BUCKETS,
)

# Worker queue wait: time from Kafka consume to worker assignment
# Reflects Alert Agent internal queuing / worker pool saturation.
WORKER_QUEUE_WAIT_DURATION = Histogram(
    'alert_bridge_worker_queue_wait_duration_seconds',
    'Time from Kafka consume to worker assignment',
    buckets=WORKER_QUEUE_WAIT_BUCKETS,
)

# VST video fetch latency
VST_DURATION = Histogram(
    'alert_bridge_vst_duration_seconds',
    'Duration of VST video fetch operation',
    buckets=VST_DURATION_BUCKETS,
)

# Video clip length: effective_end_time - effective_start_time as returned by VST.
# This is the actual window queried, after segment_anchor/segment_duration adjustments.
VIDEO_LENGTH = Histogram(
    'alert_bridge_video_length_seconds',
    'Length of the video clip requested from VST (effective end - effective start)',
    buckets=VIDEO_LENGTH_BUCKETS,
)

# VLM inference latency
VLM_DURATION = Histogram(
    'alert_bridge_vlm_duration_seconds',
    'Duration of VLM inference operation',
    buckets=VLM_DURATION_BUCKETS,
)

# Total worker processing time (worker_assigned to elastic_ready)
WORKER_PROCESSING_DURATION = Histogram(
    'alert_bridge_worker_processing_seconds',
    'Total time worker spent processing an event (worker_assigned → elastic_ready)',
    buckets=WORKER_PROCESSING_BUCKETS,
)

# End-to-end latency as seen by Alert Agent: event end → elastic_ready
# This is the most complete view of latency including overrides, because it
# is measured in-process before Elasticsearch writes (which may be overridden).
E2E_DURATION = Histogram(
    'alert_bridge_e2e_duration_seconds',
    'End-to-end latency from event end to elastic_ready (full pipeline as seen by Alert Agent)',
    buckets=E2E_DURATION_BUCKETS,
)

# Opt-in per-sensor latency histograms. The recorder observes these only when
# ``alert_agent.metrics.per_sensor_labels`` is true, so existing aggregate
# dashboards remain unchanged and deployments that do not need per-camera
# latency avoid the extra ``buckets × sensors`` series.
UPSTREAM_DURATION_BY_SENSOR = Histogram(
    'alert_bridge_upstream_duration_by_sensor_seconds',
    'Time from event end to Kafka publish, broken down by sensor',
    ['sensorId'],
    buckets=UPSTREAM_DURATION_BUCKETS,
)

KAFKA_LAG_DURATION_BY_SENSOR = Histogram(
    'alert_bridge_kafka_lag_duration_by_sensor_seconds',
    'Time from Kafka publish to Alert Agent consumption, broken down by sensor',
    ['sensorId'],
    buckets=KAFKA_LAG_BUCKETS,
)

WORKER_QUEUE_WAIT_DURATION_BY_SENSOR = Histogram(
    'alert_bridge_worker_queue_wait_duration_by_sensor_seconds',
    'Time from Kafka consume to worker assignment, broken down by sensor',
    ['sensorId'],
    buckets=WORKER_QUEUE_WAIT_BUCKETS,
)

VST_DURATION_BY_SENSOR = Histogram(
    'alert_bridge_vst_duration_by_sensor_seconds',
    'Duration of VST video fetch operation, broken down by sensor',
    ['sensorId'],
    buckets=VST_DURATION_BUCKETS,
)

VIDEO_LENGTH_BY_SENSOR = Histogram(
    'alert_bridge_video_length_by_sensor_seconds',
    'Length of the video clip requested from VST, broken down by sensor',
    ['sensorId'],
    buckets=VIDEO_LENGTH_BUCKETS,
)

VLM_DURATION_BY_SENSOR = Histogram(
    'alert_bridge_vlm_duration_by_sensor_seconds',
    'Duration of VLM inference operation, broken down by sensor',
    ['sensorId'],
    buckets=VLM_DURATION_BUCKETS,
)

WORKER_PROCESSING_DURATION_BY_SENSOR = Histogram(
    'alert_bridge_worker_processing_by_sensor_seconds',
    'Total time worker spent processing an event, broken down by sensor',
    ['sensorId'],
    buckets=WORKER_PROCESSING_BUCKETS,
)

E2E_DURATION_BY_SENSOR = Histogram(
    'alert_bridge_e2e_duration_by_sensor_seconds',
    'End-to-end latency from event end to elastic_ready, broken down by sensor',
    ['sensorId'],
    buckets=E2E_DURATION_BUCKETS,
)

# ── Counters ────────────────────────────────────────────────────────────────
#
# Reconciliation invariants (what dashboards can assert end-to-end):
#
#   raw_kafka_events = sum(EVENTS_DROPPED) + EVENTS_AFTER_DEDUP
#   EVENTS_AFTER_DEDUP = sum(EVENTS_TOTAL)
#                      + EVENTS_SKIPPED_CONFIRMED
#                      + <other early-exit skips>
#
# The first identity is complete: every pre-processing filter that drops
# a message increments ``EVENTS_DROPPED`` with the corresponding
# ``reason`` label, and every message that survives is counted in
# ``EVENTS_AFTER_DEDUP``.
#
# The second identity is **mostly** complete after C9: events that are
# skipped because a confirmed verdict already exists in Redis are now
# counted in ``EVENTS_SKIPPED_CONFIRMED``. The one remaining gap is C10:
#   - Events whose alert type has no prompt configured — will be counted
#     via ``VERIFICATION_FAILURES{reason="no_prompt"}`` in a follow-up.

# Events dropped by batch-level filters BEFORE the per-event pipeline runs.
# This is the counter operators use to distinguish "event processed
# correctly" from "event silently discarded upstream" — critical for GT
# eval pipelines where a count-match false-positive (dropped events going
# unnoticed) is the single biggest correctness risk.
#
# reason values:
#   end_time_delta – event's ``end`` timestamp is outside the configured
#                    record-time window (too old or clock-skew future)
#   dedup          – duplicate of a recently-processed event (Redis TTL)
#   rate_limit     – dropped by the per-sensor rate limiter
EVENTS_DROPPED = Counter(
    'alert_bridge_events_dropped_total',
    'Events dropped by batch-level filters before entering the per-event pipeline',
    ['reason'],
)

# ---------------------------------------------------------------------------
# Per-sensor variants (C21)
#
# These counters are structurally identical to the ones above but carry
# an extra ``sensorId`` label. They are populated ONLY when the opt-in
# config flag ``alert_agent.metrics.per_sensor_labels`` is true —
# otherwise they stay absent, costing the scrape target nothing.
#
# Rationale for keeping them separate (rather than adding a label to
# the existing metrics): Prometheus metric shape is fixed at
# construction time. Two-metric split lets small deployments opt into
# per-sensor detail without breaking any existing dashboard that
# queries the label-less aggregates, and lets large deployments stay
# safely below the ~10k-series-per-target guideline by leaving the
# opt-in off. ``sensorId`` is sanitized at the recorder boundary (see
# ``metrics.recorder._sanitize_sensor_id``) so pathological producer
# input cannot mint unbounded series.
#
# Per-sensor latency histograms use the same opt-in gate. They are defined
# above so Prometheus can expose them when observed, but the recorder only
# emits them when ``per_sensor_labels`` is enabled in config.
EVENTS_TOTAL_BY_SENSOR = Counter(
    'alert_bridge_events_by_sensor_total',
    'Total events processed, broken down by sensor',
    ['verdict', 'sensorId'],
)

EVENTS_AFTER_DEDUP_BY_SENSOR = Counter(
    'alert_bridge_events_after_dedup_by_sensor_total',
    'Events after all pre-processing filters, broken down by sensor',
    ['sensorId'],
)

EVENTS_SKIPPED_CONFIRMED_BY_SENSOR = Counter(
    'alert_bridge_events_skipped_confirmed_by_sensor_total',
    'Events skipped due to confirmed verdict, broken down by sensor',
    ['sensorId'],
)

EVENTS_DROPPED_BY_SENSOR = Counter(
    'alert_bridge_events_dropped_by_sensor_total',
    'Events dropped by batch-level filters, broken down by sensor',
    ['reason', 'sensorId'],
)

VERIFICATION_FAILURES_BY_SENSOR = Counter(
    'alert_bridge_verification_failures_by_sensor_total',
    'Verification failures by reason, broken down by sensor',
    ['reason', 'sensorId'],
)

# Events skipped because a confirmed verdict already exists in Redis.
# ``_set_message_id_and_should_skip`` short-circuits processing for any
# event whose fingerprint already has a confirmed verdict — re-verifying
# would waste VLM cycles and potentially flip a confirmed alert to
# rejected on a noisy second run. These skips are intentional and no
# work happened, so they must NOT be counted in ``EVENTS_TOTAL``; this
# dedicated counter keeps them visible on dashboards without conflating
# them with events that actually completed the pipeline.
EVENTS_SKIPPED_CONFIRMED = Counter(
    'alert_bridge_events_skipped_confirmed_total',
    'Events short-circuited because a confirmed verdict already exists in Redis',
)

# Events after deduplication (ready for processing)
EVENTS_AFTER_DEDUP = Counter(
    'alert_bridge_events_after_dedup_total',
    'Events after deduplication filter'
)

# Events processed by verdict (published to ES).
#
# EVENTS_TOTAL counts ALL pipeline completions — successful and failed alike.
# VERIFICATION_FAILURES counts only failure completions.
# These counters intentionally overlap: every failure increments both.
#
# Correct dashboard formula:
#   successful = sum(events_total) - sum(verification_failures_total)
# This holds because each failure is counted in both exactly once.
#
# Do NOT subtract per-reason verification_failures from per-verdict events_total
# without summing across all labels first — partial subtraction gives wrong numbers.
EVENTS_TOTAL = Counter(
    'alert_bridge_events_total',
    'Total events processed',
    ['verdict']
)

# Async external operation latency (VST/Elastic/Redis)
ASYNC_EXTERNAL_IO_DURATION = Histogram(
    'alert_bridge_async_external_io_duration_seconds',
    'Duration of async external operations and sync fallbacks',
    ['operation', 'mode', 'result'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0]
)

# Count of async external operations that required sync fallback
ASYNC_EXTERNAL_IO_FALLBACK_TOTAL = Counter(
    'alert_bridge_async_external_io_fallback_total',
    'Number of async external operations that fell back to sync path',
    ['operation', 'reason']
)

# In-flight async sink operations (primarily Elastic sink writes).
# ``multiprocess_mode='livesum'`` is required because Alert Bridge runs
# the FastAPI HTTP layer in a child process while the pipeline runs in
# the parent; both write to this gauge through the AsyncExternalIOMixin
# lifecycle hooks and the operator dashboard expects the *combined*
# in-flight count, not whichever process happened to win the last
# scrape race.
ASYNC_SINK_IN_FLIGHT = Gauge(
    'alert_bridge_async_sink_in_flight',
    'Current number of in-flight async sink operations',
    multiprocess_mode='livesum',
)

# ---------------------------------------------------------------------------
# Realtime alert rule metrics
# ---------------------------------------------------------------------------

# In-memory live count of realtime rules currently registered with the
# RealtimeAlertService.  Owned by the FastAPI child process; the
# ``livesum`` mode keeps the value visible on the parent's scrape
# endpoint via the multiprocess collector.
REALTIME_RULES_ACTIVE = Gauge(
    'alert_bridge_realtime_rules_active',
    'Number of currently active realtime alert rules',
    multiprocess_mode='livesum',
)

REALTIME_RULES_CREATED = Counter(
    'alert_bridge_realtime_rules_created_total',
    'Total realtime alert rules created',
)

REALTIME_RULES_DELETED = Counter(
    'alert_bridge_realtime_rules_deleted_total',
    'Total realtime alert rules deleted',
)

REALTIME_RULES_FAILED = Counter(
    'alert_bridge_realtime_rules_failed_total',
    'Realtime alert rule creation failures',
    ['stage'],
)

# ---------------------------------------------------------------------------
# Realtime persistence + replay metrics 
#
# Operators use these to monitor the rollout of the realtime persistence
# and replay  features. They are emitted only
# when ``rtvi_vlm.enable_realtime_persistence`` is true and the persistence
# layer is reachable; in-memory mode leaves them at 0 so a flat-line on
# ``alert_bridge_realtime_rules_persisted_total`` is itself a useful signal
# that the rollback flag has been flipped.
# ---------------------------------------------------------------------------

# Incremented exactly once per rule that reaches ACTIVE status durably
# in Elasticsearch — i.e. *after* the start_alert flow has landed both
# the initial PENDING create AND the follow-up update that flips the
# row to ACTIVE with the RTVI stream id attached.  Earlier intermediate
# success (PENDING-only) does NOT count: any failure in the RTVI hand-
# off rolls the PENDING row back, so incrementing on the create would
# inflate the counter for rules that never became usable.
#
# Distinct from ``REALTIME_RULES_CREATED`` which counts attempted creates
# (including in-memory-only mode). The delta between the two equals the
# rules that did NOT make it to durable, active storage.
REALTIME_RULES_PERSISTED = Counter(
    'alert_bridge_realtime_rules_persisted_total',
    'Realtime alert rules durably persisted to Elasticsearch with status=active',
)

# Durable ACTIVE rule count from Elasticsearch — refreshed on every
# successful create / delete / replay.  This is intentionally separate
# from ``REALTIME_RULES_ACTIVE`` (in-memory live count): operators can
# compare the two on the dashboard to spot drift between the live
# registry and the system of record.
#
# ``multiprocess_mode='livemostrecent'`` (NOT ``livesum``) is required
# because every FastAPI worker that handles a CRUD/replay request reads
# the same ES-derived total and writes it to the gauge.  Under a
# multi-worker uvicorn deployment ``livesum`` would silently inflate
# the displayed value by the worker count; ``livemostrecent`` returns
# the value most recently set across all live writers, which is exactly
# the "current ES state" semantic the gauge is meant to expose.  See
# the maintainer review (post-merge) for the production
# scenario this guards against.
#
# The producer side (`_refresh_rules_count_gauge`) filters the ES
# read by ``status=ACTIVE`` so PENDING rows from in-flight POSTs and
# crash-orphaned PENDINGs (lifetime up to ``pending_ttl_seconds``) do
# not pollute the operator-facing count.
REALTIME_RULES_COUNT = Gauge(
    'alert_bridge_realtime_rules_count',
    'Number of realtime alert rules with status=ACTIVE in Elasticsearch',
    multiprocess_mode='livemostrecent',
)

# Replay invocations that actually executed a replay loop, labelled by
# outcome.  Short-circuit rejections (501 persistence-disabled, 409
# in-flight) are intentionally NOT counted here — incrementing them
# would inflate the "replay rate" panel that operators use to detect
# storm-like replay traffic during incident response.  Those 501 / 409
# events are still observable via the ``replay_end`` structured log
# lines emitted before the short-circuit return.  See the design discussion
# in the maintainer review for context.
#
# outcome values (closed enum — adding new values is a deliberate change):
#   success – every rule re-onboarded
#   partial – at least one rule re-onboarded AND at least one failed
#   failed  – zero rules re-onboarded (the loop ran but every rule errored)
REPLAY_INVOCATIONS = Counter(
    'alert_bridge_replay_invocations_total',
    'Replay invocations that ran the loop, by outcome '
    '(short-circuit rejections excluded)',
    ['outcome'],
)

# Per-rule failure counter incremented inside the replay loop. Sum across
# multiple invocations gives operators "total rules that failed to
# re-onboard" without having to scrape the per-invocation details array.
REPLAY_RULE_FAILURES = Counter(
    'alert_bridge_replay_rule_failures_total',
    'Per-rule failures during replay',
)

# ---------------------------------------------------------------------------
# RTVI VLM call metrics
# ---------------------------------------------------------------------------

RTVI_CALL_DURATION = Histogram(
    'alert_bridge_rtvi_call_duration_seconds',
    'Duration of RTVI VLM HTTP calls',
    ['method'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

RTVI_CALL_FAILURES = Counter(
    'alert_bridge_rtvi_call_failures_total',
    'Failed RTVI VLM HTTP calls',
    ['method'],
)

# ---------------------------------------------------------------------------
# Incident query metrics
# ---------------------------------------------------------------------------

INCIDENT_QUERY_DURATION = Histogram(
    'alert_bridge_incident_query_duration_seconds',
    'Duration of Elasticsearch incident queries',
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

INCIDENT_QUERY_FAILURES = Counter(
    'alert_bridge_incident_query_failures_total',
    'Failed Elasticsearch incident queries',
)

# Verification failures broken down by failure stage/reason.
# Incremented for every event that does NOT produce a successful VLM verdict,
# including events that never reach the VLM (VST failures, URL validation) and
# events where the VLM responded but the response could not be used.
#
# VST-side reason values (symmetric with the VLM side — see the
# ``_classify_vst_failure_reason`` helper in ``enhance_alert_with_vlm.py``
# for the exact mapping from exception types). Dashboards previously
# filtering on ``reason="vst_failure"`` should migrate to
# ``reason=~"vst_.*"``:
#   vst_timeout          – VST request timed out (VSTTimeoutError)
#   vst_overloaded       – VST returned 503 overloaded (VSTOverloadedError)
#   vst_not_found        – no recording for the requested time (VSTRecordingNotFoundError, or a None-URL success)
#   vst_unavailable      – VST service unreachable (VSTUnavailableError)
#   vst_client_error     – VST rejected the request as 4xx (VSTClientError)
#   vst_server_error     – bare VSTError / 5xx / missing_video_url response
#   vst_unknown          – non-VSTError exception on the VST path
#
# URL / VLM reason values:
#   url_validation       – video URL failed HTTP reachability check
#   vlm_parse_failure    – VLM responded but response could not be parsed/validated
#   vlm_timeout          – VLM request timed out after all retries
#   vlm_connection_error – could not connect to VLM service after all retries
#   vlm_server_error     – VLM returned 5xx internal server error after all retries
#   vlm_invalid_payload  – VLM rejected the request as unprocessable (422)
#   no_prompt            – alert type has no prompt configured (early-exit
#                          before any VST/VLM work; see C10)
#   redis_unavailable    – Redis failure during the confirmed-verdict skip
#                          check (pre-pipeline early-exit; see C25)
#   unknown              – unexpected exception not covered above
VERIFICATION_FAILURES = Counter(
    'alert_bridge_verification_failures_total',
    'Verification failures by stage/reason',
    ['reason']
)

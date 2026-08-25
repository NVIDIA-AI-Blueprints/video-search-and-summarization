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
WORKER_START_WAIT_BUCKETS = [0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]
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

# Worker start wait: time after scheduler assignment until per-event processing starts.
# Includes sub-batch head-of-line blocking, pre-processing, and async dispatch backlog.
WORKER_START_WAIT_DURATION = Histogram(
    'alert_bridge_worker_start_wait_duration_seconds',
    'Time from worker assignment to per-event processing start',
    buckets=WORKER_START_WAIT_BUCKETS,
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

# Total worker processing time (per-event processing start to elastic_ready)
WORKER_PROCESSING_DURATION = Histogram(
    'alert_bridge_worker_processing_seconds',
    'Total time spent processing an event (processing start → elastic_ready)',
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

# On-demand API metrics intentionally use a separate namespace from the Kafka
# pipeline metrics above.  Mixing HTTP requests into EVENTS_AFTER_DEDUP /
# EVENTS_TOTAL would invalidate the Kafka reconciliation invariants documented
# below.
ONDEMAND_VLM_DURATION = Histogram(
    'alert_bridge_ondemand_vlm_duration_seconds',
    'Duration of a VLM inference attempt initiated by the on-demand API',
    buckets=VLM_DURATION_BUCKETS,
)

ONDEMAND_PROCESSING_DURATION = Histogram(
    'alert_bridge_ondemand_processing_seconds',
    'Time spent processing and publishing an accepted on-demand request',
    buckets=WORKER_PROCESSING_BUCKETS,
)

ONDEMAND_E2E_DURATION = Histogram(
    'alert_bridge_ondemand_e2e_duration_seconds',
    'Time from on-demand HTTP request entry to background publish completion',
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

WORKER_START_WAIT_DURATION_BY_SENSOR = Histogram(
    'alert_bridge_worker_start_wait_duration_by_sensor_seconds',
    'Time from worker assignment to per-event processing start, broken down by sensor',
    ['sensorId'],
    buckets=WORKER_START_WAIT_BUCKETS,
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
# skipped because a confirmed verdict already exists (verdict protection,
# ES-backed) are now counted in ``EVENTS_SKIPPED_CONFIRMED``. The one
# remaining gap is C10:
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
#   dedup          – duplicate of a recently-processed event (in-process TTL)
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

# Events skipped because a confirmed verdict already exists (verdict
# protection, backed by Elasticsearch).
# ``_set_message_id_and_should_skip`` short-circuits processing for any
# event whose fingerprint already has a confirmed verdict — re-verifying
# would waste VLM cycles and potentially flip a confirmed alert to
# rejected on a noisy second run. These skips are intentional and no
# work happened, so they must NOT be counted in ``EVENTS_TOTAL``; this
# dedicated counter keeps them visible on dashboards without conflating
# them with events that actually completed the pipeline.
EVENTS_SKIPPED_CONFIRMED = Counter(
    'alert_bridge_events_skipped_confirmed_total',
    'Events short-circuited because a confirmed verdict already exists (verdict protection)',
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

# On-demand request accounting is separate from Kafka event accounting.  The
# outcome and reason labels are constrained by metrics.recorder allowlists.
ONDEMAND_REQUESTS_TOTAL = Counter(
    'alert_bridge_ondemand_requests_total',
    'On-demand HTTP requests by synchronous acceptance outcome',
    ['outcome'],
)

ONDEMAND_EVENTS_TOTAL = Counter(
    'alert_bridge_ondemand_events_total',
    'Accepted on-demand events that completed background processing',
    ['verdict'],
)

ONDEMAND_VERIFICATION_FAILURES = Counter(
    'alert_bridge_ondemand_verification_failures_total',
    'On-demand background verification failures by stable reason',
    ['reason'],
)

# Async external operation latency (VST/Elastic/in-process dedup state)
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

# In-flight dispatched messages (thread_bridge executor tasks or event_loop
# coroutines), i.e. the live occupancy of the backpressure semaphore.
# ─── Multi-process fleet ────────────────────────────────────────────────────
# Degraded capacity is invisible from throughput alone: an instance short of a
# process, or holding no partitions, keeps serving whatever it still owns.
# Bounded cardinality on purpose -- no per-partition or per-process labels, so
# the series count does not grow with the fleet.
PIPELINE_PROCESSES_CONFIGURED = Gauge(
    'alert_bridge_pipeline_processes_configured',
    'Pipeline processes this instance was configured to run',
    multiprocess_mode='livemostrecent',
)

PIPELINE_PROCESSES_ALIVE = Gauge(
    'alert_bridge_pipeline_processes_alive',
    'Pipeline processes currently running',
    multiprocess_mode='livemostrecent',
)

PIPELINE_PROCESSES_READY = Gauge(
    'alert_bridge_pipeline_processes_ready',
    'Pipeline processes that have been given a partition assignment',
    multiprocess_mode='livemostrecent',
)

PIPELINE_PROCESS_EXITS = Counter(
    'alert_bridge_pipeline_process_exits_total',
    'Pipeline processes that exited, by whether a shutdown was asked for',
    ['reason'],
)

ASSIGNED_PARTITIONS = Gauge(
    'alert_bridge_assigned_partitions',
    'Source partitions currently assigned to this instance',
    multiprocess_mode='livesum',
)

REBALANCE_DRAINS = Counter(
    'alert_bridge_rebalance_drains_total',
    'Drains of revoked partitions, by whether they finished in time',
    ['outcome'],
)

RECORDS_READ_AFTER_REVOKE = Counter(
    'alert_bridge_records_read_after_revoke_total',
    'Records read for a partition this member had already lost. Read with '
    'alert_bridge_rebalance_drains_total: a drain reports only what it could '
    'wait for, and these arrived too late for it to know about',
)

DISPATCH_IN_FLIGHT = Gauge(
    'alert_bridge_dispatch_in_flight',
    'Current number of in-flight dispatched messages',
    multiprocess_mode='livesum',
)

# Time a message spent between dispatch submission and the moment its task
# started executing (event_loop mode: taskDispatchedAt -> taskStartedAt).
DISPATCH_WAIT_DURATION = Histogram(
    'alert_bridge_dispatch_wait_seconds',
    'Wait between dispatch submission and task execution start',
    buckets=WORKER_QUEUE_WAIT_BUCKETS,
)

# Time a task spent waiting on a per-service concurrency capacity slot
# (event_loop mode semaphores), kept separate from processing duration.
CAPACITY_WAIT_DURATION = Histogram(
    'alert_bridge_capacity_wait_seconds',
    'Wait for a per-service concurrency slot before issuing the call',
    ['service'],
    buckets=WORKER_QUEUE_WAIT_BUCKETS,
)

# Live per-service concurrency in event_loop mode (calls currently holding a
# capacity slot). Bounded by max_vlm_concurrent / max_vst_concurrent.
EVENT_LOOP_VLM_IN_FLIGHT = Gauge(
    'alert_bridge_event_loop_vlm_in_flight',
    'Concurrent VLM calls holding a capacity slot in event_loop mode',
    multiprocess_mode='livesum',
)

EVENT_LOOP_VST_IN_FLIGHT = Gauge(
    'alert_bridge_event_loop_vst_in_flight',
    'Concurrent VST calls holding a capacity slot in event_loop mode',
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

# --- Realtime RT-VLM alert consolidation (read-time dedup) -------------------
# Emitted by IncidentService._consolidate when consolidate=true. Let operators
# tune max_inter_alert_gap_seconds / max_event_duration_seconds empirically.
# Aggregate-only (no sensorId/category labels): the consolidator labels straight
# from Elasticsearch documents, so per-sensor labels would mint unbounded,
# permanent Prometheus series on high-cardinality or malformed sensor IDs. The
# aggregate chunks-in / events-out ratio is the dedup-effectiveness signal; a
# per-sensor breakdown, if ever needed, must go through the gated + sanitized
# per-sensor path (metrics.recorder: per_sensor_labels_enabled / _sanitize_sensor_id).
DEDUP_CHUNKS_IN = Counter(
    'alert_bridge_dedup_chunks_in_total',
    'Raw RT-VLM chunk incidents fed into the consolidator',
)

DEDUP_EVENTS_OUT = Counter(
    'alert_bridge_dedup_events_out_total',
    'Consolidated events emitted by the consolidator',
)

# reason values: gap (continuity break) | outer (duration cap) | malformed
DEDUP_SPLIT_REASON = Counter(
    'alert_bridge_dedup_split_reason_total',
    'Why an event ended and a new one began, by reason',
    ['reason'],
)

DEDUP_DURATION = Histogram(
    'alert_bridge_dedup_duration_seconds',
    'Time spent folding chunks into events (excludes the Elasticsearch query)',
    buckets=[0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
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
#   redis_unavailable    – legacy label name, retained for metric-contract
#                          back-compat; emitted on a backend failure during
#                          the confirmed-verdict skip check (pre-pipeline
#                          early-exit; see C25). Verdict protection is now
#                          Elasticsearch-backed.
#   unknown              – unexpected exception not covered above
VERIFICATION_FAILURES = Counter(
    'alert_bridge_verification_failures_total',
    'Verification failures by stage/reason',
    ['reason']
)

# ---------------------------------------------------------------------------
# In-process store + verdict-protection observability
#
# These four families give operators the visibility the Redis removal took
# away: bounded-cardinality signals for the in-process caches (memory
# growth), the ES-backed verdict protection (latency + fail-open), the
# alert-config read-through composite (where reads are served from), and the
# ES index readiness + producer partition-key alignment guardrail.
#
# Cardinality is deliberately bounded: ``store`` ∈ {dedup, enddelta,
# alert_config}, ``reason``/``source`` are closed enums, and NO sensorId /
# fingerprint / objectId is ever used as a label.
# ---------------------------------------------------------------------------

# (1) In-process cache occupancy + eviction accounting.
# ``livemostrecent`` is deliberate and stays that way under
# ``alert_agent.processes > 1``. The two labels aggregate differently and no
# single mode is right for both: ``dedup``/``enddelta`` are partitioned
# (each process holds a disjoint set of sensor cohorts, so the instance total
# is the sum), while ``alert_config`` is replicated identically in every
# process including the FastAPI child, so summing it would report N times the
# real config count. This gauge therefore reads as a per-process sample:
# still a valid memory-growth signal, since every process's cache grows
# alike, but not an instance-wide total. Use ``sum(...) by (store)`` in the
# query for that, on the partitioned stores only.
DEDUP_CACHE_OCCUPANCY = Gauge(
    'alert_bridge_dedup_cache_occupancy',
    'Resident entry count of an in-process state cache (live entries plus any '
    'expired-but-not-yet-swept; the 30s sweep reconciles it to the live count)',
    ['store'],
    multiprocess_mode='livemostrecent',
)

DEDUP_CACHE_EVICTIONS = Counter(
    'alert_bridge_dedup_cache_evictions_total',
    'In-process cache entries evicted because their TTL expired',
    ['store', 'mode'],  # mode ∈ {lazy, sweep}
)

# (2) Confirmed-verdict protection (ES-backed).
VERDICT_ES_GET_DURATION = Histogram(
    'alert_bridge_verdict_es_get_duration_seconds',
    'Latency of the confirmed-verdict marker ES get',
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)

# Fail-open events: a verdict check that could NOT positively confirm and so
# let the event proceed to VLM. reason ∈ closed enum below — never labelled
# with sensorId/fingerprint.
#   es_down    – ES client unavailable / disabled by backoff
#   error      – ES get raised
#   expired    – marker present but expired
#   malformed  – marker present but expires_at missing / non-numeric / non-finite
VERDICT_FAIL_OPEN = Counter(
    'alert_bridge_verdict_fail_open_total',
    'Confirmed-verdict checks that failed open (event allowed to proceed)',
    ['reason'],
)

# (3) Alert-config read-through composite: where each read was served from.
#   cache    – in-process hot cache hit
#   es       – Elasticsearch (source of truth)
#   snapshot – last-resort in-memory snapshot after an ES error
ALERT_CONFIG_READ_SOURCE = Counter(
    'alert_bridge_alert_config_read_source_total',
    'Alert-config reads by the store tier that served them',
    ['source'],
)

# Seconds since the alert-config in-memory snapshot was last refreshed from
# ES. A rising value means the composite has been serving without a
# successful ES read (staleness risk).
ALERT_CONFIG_STALENESS_SECONDS = Gauge(
    'alert_bridge_alert_config_staleness_seconds',
    'Seconds since the alert-config snapshot was last refreshed from Elasticsearch',
    multiprocess_mode='livemostrecent',
)

# (4a) ES index readiness. 1 = index confirmed present at startup, 0 = not
# ready / creation failed. index ∈ {confirmed-verdicts, alert-configs, ...}.
INDEX_READY = Gauge(
    'alert_bridge_index_ready',
    'Whether a required Elasticsearch index has been confirmed ready (1) or not (0)',
    ['index'],
    multiprocess_mode='livemostrecent',
)

# (4b) Producer partition-key alignment guardrail. Dedup determinism relies
# on the Kafka record key beginning with sensorId; this counter surfaces
# producer-side drift. aligned ∈ {yes, no, unknown}. No sensorId label.
RECORD_KEY_ALIGNMENT = Counter(
    'alert_bridge_record_key_alignment_total',
    'Consumed records by whether their Kafka record key aligned with sensorId',
    ['aligned'],
)

# ---------------------------------------------------------------------------
# Confirmed-verdict marker retention (hourly throttled reaper)
# ---------------------------------------------------------------------------

# Markers deleted by the retention job (cumulative).
VERDICT_RETENTION_DELETED = Counter(
    'alert_bridge_verdict_retention_deleted_total',
    'Expired confirmed-verdict markers deleted by the retention job',
)

# Retention-job runs by outcome ∈ {success, failure}.
VERDICT_RETENTION_RUNS = Counter(
    'alert_bridge_verdict_retention_runs_total',
    'Confirmed-verdict retention job runs by outcome',
    ['outcome'],
)

# Unix timestamp of the last successful retention run (staleness / liveness).
VERDICT_RETENTION_LAST_RUN = Gauge(
    'alert_bridge_verdict_retention_last_run_timestamp_seconds',
    'Unix timestamp of the last successful confirmed-verdict retention run',
    multiprocess_mode='livemostrecent',
)

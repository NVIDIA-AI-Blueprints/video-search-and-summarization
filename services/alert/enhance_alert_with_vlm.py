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

import os as _os
import sys as _sys
# Bootstrap: this launcher lives at the service root while the packages live
# under ``src/``. Put ``src/`` on the import path so ``import vlm`` etc. resolve
# both locally and inside the container (Dockerfile keeps CMD at /app).
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "src"))

import argparse
import contextlib
import asyncio
from functools import partial
import json
import os
import signal
import sys
import time
import threading
from datetime import datetime, timezone
import multiprocessing
from queue import Queue, Empty
from typing import Any, Callable, Dict, List, Optional, Set, TYPE_CHECKING

from concurrent.futures import ThreadPoolExecutor, Future, TimeoutError as FutureTimeoutError

import requests
import uvicorn
import yaml
from openai import APIConnectionError, APITimeoutError, InternalServerError, UnprocessableEntityError
from openai.types.chat import ChatCompletionMessage

from metrics import PROMETHEUS_ENABLED
if PROMETHEUS_ENABLED:
    from metrics import reset_prometheus_multiproc_dir
    reset_prometheus_multiproc_dir()

from clients.redis_handler import RedisHandler
from mdx.event_bridge_factory import EventBridgeFactory
from vst.exceptions import (
    VSTError,
    VSTClientError,
    VSTOverloadedError,
    VSTRecordingNotFoundError,
    VSTTimeoutError,
    VSTUnavailableError,
)
from mdx.sink.vlm_enhanced_sink import build_vlm_enhanced_sink
from schemas.vlm_responses import (
    AlertBridgeResponse,
    VLMResponse,
    merge_info_with_response,
)
from schemas.base_response_parser import load_response_parser
from schemas.pluggable_parser_runtime import (
    ERROR_SOURCE_MEDIA_DOWNLOAD,
    ERROR_SOURCE_VLM_API,
    ERROR_SOURCE_VLM_SCHEMA,
    PLUGGABLE_PARSER_ERROR_STATUS,
    PLUGGABLE_PARSER_OK_STATUS,
    apply_pluggable_parser_error as _apply_pluggable_parser_error,
    apply_pluggable_parser_output as _apply_pluggable_parser_output,
    ERROR_SOURCE_PLUGGABLE_PARSER,
    safe_json_dumps_parser_output as _safe_json_dumps_parser_output,
)
if TYPE_CHECKING:
    from webhook import OpenClawNotifier, WebhookKafkaForwarder

# Backwards-compatible module-level aliases for the legacy private names
# (``_PLUGGABLE_PARSER_OK_STATUS`` / ``_PLUGGABLE_PARSER_ERROR_STATUS``).
# External tests and a handful of diagnostic scripts still read these via
# ``enhance_alert_with_vlm._PLUGGABLE_PARSER_OK_STATUS``; the helpers and
# constants themselves now live in :mod:`schemas.pluggable_parser_runtime`
# so Mode-3 (``DirectMediaHandler``) can import them at module load time
# without paying a circular-import lazy-import penalty.
_PLUGGABLE_PARSER_OK_STATUS = PLUGGABLE_PARSER_OK_STATUS
_PLUGGABLE_PARSER_ERROR_STATUS = PLUGGABLE_PARSER_ERROR_STATUS

# Covers one pipeline construction plus a group join, per child, while several
# of them contend on Elasticsearch. Only bounds how long readiness is awaited;
# a child that arrives later still runs and still serves its partitions.
# Readiness has no budget of its own: it draws from the one startup budget,
# minus whatever the steps before it already spent.
from handlers.enrichment import EnrichmentProcessor
from handlers.direct_media import DirectMediaHandler
from handlers.async_dispatch_mixin import (
    AsyncDispatchMixin,
    PIPELINE_MODE_EVENT_LOOP,
    PIPELINE_MODE_SYNC,
    PIPELINE_MODE_THREAD_BRIDGE,
    resolve_pipeline_mode,
)
from handlers.async_external_io_mixin import AsyncExternalIOMixin
from handlers.async_vlm_mode_mixin import AsyncVLMModeMixin
from handlers.event_loop_pipeline_mixin import EventLoopPipelineMixin
from utils.event_utils import normalize_alert_message, is_alert
from utils.partition_in_flight import DEFAULT_DRAIN_TIMEOUT, PartitionInFlight
from utils import startup_deadline
from utils import fleet_state
from utils.startup_deadline import StartupDeadline
from utils.process_scaling import (
    MINIMUM_STARTUP_TIMEOUT_SECONDS, await_source_partitions,
    resolve_process_count, startup_timeout,
)

# Reserved for the fleet to join its groups. Imported rather than repeated:
# process_scaling validates the whole budget against this same number, and two
# copies would let a change to one silently defeat the check in the other.
MIN_FLEET_WAIT_SECONDS = MINIMUM_STARTUP_TIMEOUT_SECONDS

# The watchdog is the floor under the readiness wait, so it has to end after
# it. Extended by exactly the same amount the announce window was widened by,
# the two ended in a dead heat decided by microseconds of thread start-up --
# which is not a relationship worth resting on.
WATCHDOG_MARGIN_SECONDS = 5.0

# How long shutdown waits for the event-loop HTTP and Elasticsearch clients to
# close. Short on purpose: this runs between two bounded steps of a teardown
# the container is timing, and a client that will not close is not worth the
# whole grace period.
CLIENT_CLOSE_TIMEOUT = 5.0
from utils.process_supervisor import DRAIN_SECONDS, ProcessSupervisor
from utils.url_transformer import transform_video_url, is_vlm_local
from mdx.utils.elastic_ready import generate_alert_fingerprint, generate_incident_fingerprint
from utils.logging_config import setup_logging, get_logger, enforce_log_level
from utils.schema_util import protobuf_anomalies_to_json_string_list
from vlm.vlm_client import VLMClient, AsyncVLMRuntime
from vlm.warmup import warmup_vlm, WARMUP_VIDEO
from metrics.recorder import (
    inc_events_after_dedup,
    inc_events_dropped,
    inc_events_skipped_confirmed,
    observe_video_length,
    observe_vlm_duration,
    observe_vst_duration,
    record_event_complete,
    set_per_sensor_labels,
    warm_startup_labels,
)
from utils.time_utils import iso_delta_seconds, parse_iso_utc
if PROMETHEUS_ENABLED:
    # ``ASYNC_SINK_IN_FLIGHT`` is still referenced directly here: it is a
    # gauge written from two lifecycle hooks (init + shutdown) on this
    # class in addition to the per-operation updates in
    # ``AsyncExternalIOMixin``, and none of those sites map cleanly onto
    # a recorder helper.
    from metrics.prometheus_metrics import ASYNC_SINK_IN_FLIGHT
    from prometheus_client import CollectorRegistry
    from prometheus_client import multiprocess as prometheus_multiprocess
    from prometheus_client import start_http_server as start_prometheus_server

# Configure centralized logging from config.yaml
setup_logging()
logger = get_logger(__name__)

# Set once the multi-process branch is active, so the shutdown signal handler
# can tear the children down before the parent exits.
_pipeline_supervisor: Optional[ProcessSupervisor] = None


def _dropped_messages(before, after):
    """Return the list of messages in ``before`` but not in ``after``.

    Used by the C21 per-sensor drop counters to break filter drops
    down by ``sensorId``. Comparison is by object identity (``is``) —
    the filters return the same dict references for kept messages, so
    identity-based set difference is correct and cheap. We walk the
    lists manually rather than use ``set()`` because dicts are
    unhashable, and the batch size is small (O(100)) so O(n*m) is
    fine in practice.

    Returns an empty list when nothing was dropped — the recorder
    helpers short-circuit on zero-count anyway, so there's no metric
    emission on the happy path.
    """
    if len(before) == len(after):
        return []
    after_ids = {id(msg) for msg in after}
    return [msg for msg in before if id(msg) not in after_ids]


# Ordered VLM API error classification: (exception type, (response code,
# status prefix, failure reason, log label)). Timeout must stay ahead of
# connection error because openai's APITimeoutError subclasses it.
_VLM_API_ERROR_CLASSIFICATION = (
    (APITimeoutError, (504, "VLM request timed out", "vlm_timeout", "VLM timeout")),
    (APIConnectionError, (503, "Failed to connect to VLM service", "vlm_connection_error", "VLM connection error")),
    (InternalServerError, (500, "VLM service internal error", "vlm_server_error", "VLM server error")),
    (UnprocessableEntityError, (422, "Invalid VLM request payload", "vlm_invalid_payload", "VLM invalid payload")),
)


def validate_drain_fits_poll_interval(config: dict) -> None:
    """Refuse a poll interval the rebalance drain could outlast.

    Not part of the multi-process checks: the revoke hook that runs the drain
    is installed for every deployment, so a single-process instance evicts
    itself on the same configuration the multi-process one refuses to start on.
    """
    raw = (config.get("kafka", {}) or {}).get("max_poll_interval_ms")
    if raw is None:
        return
    try:
        poll_interval_ms = float(raw)
    except (TypeError, ValueError):
        # An unsubstituted template. Reported here rather than as a bare
        # conversion error from inside the entry point's catch-all.
        raise ValueError(
            f"kafka.max_poll_interval_ms must be a number, got {raw!r}"
        )
    if poll_interval_ms > DEFAULT_DRAIN_TIMEOUT * 1000:
        return
    # A drain runs on the consume thread inside the rebalance callback, so one
    # that outlasts the poll interval costs the member its place and starts
    # another rebalance -- worse than the overlap it exists to prevent, and
    # self-perpetuating under load.
    raise ValueError(
        f"kafka.max_poll_interval_ms={raw} is not longer than the "
        f"{DEFAULT_DRAIN_TIMEOUT:.0f}s rebalance drain. The drain runs inside "
        f"the rebalance callback, so the member would be evicted mid-drain and "
        f"trigger another rebalance. Raise max_poll_interval_ms above "
        f"{DEFAULT_DRAIN_TIMEOUT * 1000:.0f}."
    )


def validate_multi_process_config(config: dict, process_count: int) -> None:
    """Refuse a multi-process configuration the deployment cannot honour.

    Module level so it can be tested. Inside the entry point it was reachable
    only by starting the program, so the refusals, their wording and the fact
    that they fire before anything is spawned were all unverified.
    """
    source_type = str(
        (config.get("event_bridge", {}) or {}).get("sourceType", "kafka")
    ).lower()
    if source_type != "kafka":
        # Caught here rather than by the partition wait, which reads Kafka
        # metadata and would spend its whole budget before failing with a
        # message about topics the deployment does not have. The parallelism
        # also has no basis without partitions: nothing would give each process
        # a disjoint set of sensors, so their dedup state would diverge.
        raise ValueError(
            f"alert_agent.processes={process_count} requires "
            f"event_bridge.sourceType='kafka', got {source_type!r}. Work is "
            f"split across processes by partition assignment, which only a "
            f"Kafka source provides."
        )

    if not _alert_config_store_is_shared(config):
        raise ValueError(
            f"alert_agent.processes={process_count} requires shared "
            f"alert-config storage, but persistence.enabled is false. That "
            f"store is private to each process, so the supervisor cannot "
            f"initialise one the workers will read: each would build its own "
            f"copy and they would drift apart. Enable persistence, or run a "
            f"single process."
        )

    mode = pipeline_mode_from_config(config)
    if mode != PIPELINE_MODE_EVENT_LOOP:
        raise ValueError(
            f"alert_agent.processes={process_count} requires "
            f"pipeline_mode={PIPELINE_MODE_EVENT_LOOP!r}, got {mode!r}. The "
            f"other modes hold their concurrency in threads, so several "
            f"processes multiply the load on the VLM and VST backends by the "
            f"process count without the per-process caps that bound it."
        )


def _batch_partition(batch: dict):
    """The (topic, partition) a batch came from, or None for other sources."""
    topic, partition = batch.get("topic"), batch.get("partition")
    if topic is None or partition is None:
        return None
    return (topic, partition)


def pipeline_mode_from_config(config: dict) -> str:
    """Resolve the effective pipeline mode from a parsed config.

    Shared with the supervising process, which has to read the mode before it
    can decide whether several processes are allowed, and must not build a
    pipeline to find out.
    """
    alert_agent = (config or {}).get("alert_agent", {}) or {}
    async_io_cfg = alert_agent.get("async_io", {}) or {}

    # Accepted at both alert_agent.pipeline_mode and the nested
    # alert_agent.async_io.pipeline_mode; conflicting values fail startup.
    raw_mode = alert_agent.get("pipeline_mode")
    nested_mode = async_io_cfg.get("pipeline_mode")
    if raw_mode is not None and nested_mode is not None \
            and str(raw_mode).strip().lower() != str(nested_mode).strip().lower():
        raise ValueError(
            "Conflicting pipeline_mode values: "
            f"alert_agent.pipeline_mode={raw_mode!r} vs "
            f"alert_agent.async_io.pipeline_mode={nested_mode!r}"
        )
    if raw_mode is None:
        raw_mode = nested_mode

    return resolve_pipeline_mode(raw_mode, bool(async_io_cfg.get("enabled", False)))


def _pipeline_mp_context():
    """Start children with a fresh interpreter rather than a fork of this one.

    By the time the pipeline processes are started this process has a
    Prometheus HTTP server thread, a readiness thread and a FastAPI child of
    its own. Forking copies the address space but only the calling thread, so
    a child inherits locks, sockets and logging handlers mid-use with nobody
    left to release them. Spawn costs a re-import per child, which is small
    beside the pipeline each one builds anyway.
    """
    return multiprocessing.get_context("spawn")


def seed_prompt_store(config_path: str) -> None:
    """Write the startup prompts once, from the supervising process.

    Builds nothing but the PromptManager, which reaches Elasticsearch and not
    Kafka, so the parent still never joins the consumer group.
    """
    from handlers.prompt_handler.prompt_manager import PromptManager

    logger.info("Seeding the prompt store before starting pipeline processes")
    PromptManager(config_path, seed_prompts=True)


def _alert_config_store_is_shared(config: dict) -> bool:
    """Whether every process sees the same alert-config store.

    Elasticsearch-backed means shared; ``persistence.enabled: false`` falls
    back to a store private to each process (see the alert_config factory).
    """
    return bool((config.get("persistence") or {}).get("enabled", True))


class AnomalyEnhancer(
    AsyncDispatchMixin,
    AsyncExternalIOMixin,
    AsyncVLMModeMixin,
    EventLoopPipelineMixin,
):
    #: Whether this pipeline reports the fleet counts itself. True is the
    #: single-process default -- a lone pipeline is the whole fleet, and if it
    #: never publishes, a healthy instance reports 503 for its whole life. The
    #: multi-process child entry point sets it False per instance, because
    #: there the supervisor owns the totals and a child writing 1/1 would
    #: overwrite them. Declared here rather than assigned in __init__ so the
    #: default is something a test can read without building a pipeline.
    _publishes_own_fleet_state = True

    def __init__(self, config_file="config.yaml", instance_leader: bool = True,
                 seed_shared_store: bool = True):
        """Build one complete pipeline.

        ``instance_leader`` marks the single process responsible for the
        verdict-retention reaper, which belongs to the instance rather than to
        a pipeline: running it in each child would defeat its own
        request-rate throttle. Defaults to True so a single-process
        deployment is unchanged.

        ``seed_shared_store`` is cleared for a pipeline whose supervisor has
        already written the prompts, which is how a multi-process instance
        seeds once before any child can read.
        """
        self.instance_leader = instance_leader
        self.config = self.load_config(config_file)
        logger.debug("Configuration loaded: %s", list(self.config.keys()))

        # Validate event bridge configuration
        if not EventBridgeFactory.validate_configuration(self.config):
            raise ValueError("Invalid event bridge configuration")

        # Create source and sink using factory pattern
        self.source = EventBridgeFactory.create_source(self.config)
        # Dedup state lives in this process, so two members working the same
        # sensor cannot see each other. A rebalance creates exactly that, so
        # the outgoing member finishes what it owes on a partition before
        # letting it move.
        self._partition_in_flight = PartitionInFlight()
        # One budget per rebalance, not per consumer. Every consumer in this
        # process is revoked together and drains sequentially on the consume
        # thread, so a per-consumer bound multiplies by the topic count against
        # a poll interval that does not.
        self._rebalance_drain_deadline: Optional[float] = None
        self.source.set_revoke_hook(self._drain_revoked_partitions)
        # Registered here rather than only in the multi-process entry point,
        # because the revoke hook above is installed for every deployment: a
        # single-process instance drains too, and without this its budget
        # stayed spent after the first rebalance, so every later drain gave up
        # at once and reported a timeout it had never waited for. The
        # multi-process path replaces this with a hook that also signals the
        # supervisor.
        self.source.set_assignment_change_hook(self._publish_assignment_state)
        self.sink = EventBridgeFactory.create_sink(self.config)

        # Get source type for logging
        self.source_type = self.config.get('event_bridge', {}).get('sourceType', 'unknown')

        # Initialize the in-process dedup/verdict-protection state handler
        # early so it can be shared with the VLM sink. (No Redis: dedup /
        # filter state is in-process; verdict protection is ES-backed.)
        self.redis_handler = RedisHandler(config_file)

        # PromptManager has to come before the sink build so its
        # AlertConfigStore (the same ES-backed store the verification API
        # writes to) can be threaded into the sink. Without this the
        # sink would have no live source for output_category and would
        # silently use the startup file mapping instead.
        from handlers.prompt_handler.prompt_manager import PromptManager
        # A store that is not shared cannot be seeded ahead of this process:
        # with persistence disabled every process owns a private in-memory
        # copy, so each has to write its own or every prompt lookup for its
        # partitions raises -- there is no file fallback behind the store.
        self.prompt_manager = PromptManager(
            config_file,
            seed_prompts=seed_shared_store or not _alert_config_store_is_shared(self.config),
        )
        logger.info("PromptManager initialized successfully")

        # Build a single VLM enhanced sink (handles both incident and alert)
        # Pass redis_handler for verdict protection and the
        # PromptManager-owned AlertConfigStore so PUT API edits to
        # output_category hot-reload on the next publish.
        self.vlm_enhanced_event_sink = build_vlm_enhanced_sink(
            self.config,
            redis_handler=self.redis_handler,
            alert_config_store=getattr(
                self.prompt_manager, "alert_config_store", None
            ),
        )

        # Create the confirmed-verdict marker index up front (before any
        # traffic) so a mapping/creation problem surfaces at startup and the
        # index-readiness gauge reflects the real state, rather than the
        # index only appearing on the first confirmed write. Non-fatal: a
        # transient ES outage here leaves verdict protection to fail open and
        # retry via the handler's backoff path.
        self._verdict_retention_job = None
        try:
            if self.redis_handler is not None:
                self.redis_handler.ensure_verdict_index()
        except Exception as e:
            logger.warning("Verdict index startup ensure failed (non-fatal): %s", e)

        # Start the hourly throttled reaper for expired
        # confirmed-verdict markers so ``ab-confirmed-verdicts`` does not grow
        # unbounded. Only runs when verdict protection is enabled.
        try:
            if instance_leader and getattr(self.redis_handler, "_protect_confirmed_enabled", False):
                from clients.verdict_retention import (
                    DEFAULT_INTERVAL_SECONDS,
                    DEFAULT_REQUESTS_PER_SECOND,
                    VerdictRetentionJob,
                )
                _protect_cfg = (
                    self.config.get('alert_agent', {})
                    .get('event_filters', {})
                    .get('protect_confirmed_verdicts', {})
                )
                self._verdict_retention_job = VerdictRetentionJob(
                    self.redis_handler,
                    interval_seconds=_protect_cfg.get(
                        'retention_interval_seconds', DEFAULT_INTERVAL_SECONDS
                    ),
                    requests_per_second=_protect_cfg.get(
                        'retention_requests_per_second', DEFAULT_REQUESTS_PER_SECOND
                    ),
                )
                self._verdict_retention_job.start()
        except Exception as e:
            logger.warning("Verdict retention job failed to start (non-fatal): %s", e)

        self.num_workers = self.config.get('alert_agent', {}).get(
            'num_workers', 1)  # Default to sequential
        self.worker_queue = Queue(maxsize=self.num_workers)

        # C21: opt-in per-sensor label breakdown on the event-accounting
        # counters. Off by default so large deployments stay below the
        # ~10k-series-per-target Prometheus guideline; small
        # deployments / eval setups flip it on for GT triage.
        per_sensor_labels = (
            self.config.get('alert_agent', {})
            .get('metrics', {})
            .get('per_sensor_labels', False)
        )
        set_per_sensor_labels(bool(per_sensor_labels))
        if per_sensor_labels and not PROMETHEUS_ENABLED:
            logger.warning(
                "alert_agent.metrics.per_sensor_labels is true but "
                "PROMETHEUS_METRICS_ENABLED is not set — per-sensor labels "
                "will have no effect until Prometheus is enabled."
            )
        logger.info(
            "Per-sensor metric labels are %s",
            "enabled" if per_sensor_labels else "disabled",
        )

        async_io_cfg = self.config.get('alert_agent', {}).get('async_io', {}) or {}
        # ``pipeline_mode`` is accepted at both alert_agent.pipeline_mode and
        # alert_agent.async_io.pipeline_mode; conflicting values fail startup.
        # Shared with the supervising process, which has to read the mode
        # before it can decide whether multiple processes are allowed.
        self.pipeline_mode = pipeline_mode_from_config(self.config)
        # ``async_io_enabled`` gates the thread_bridge machinery only; the
        # event_loop mode awaits external I/O on the pipeline loop instead of
        # routing it through the per-service guardrail wrappers.
        event_loop_mode = self.pipeline_mode == PIPELINE_MODE_EVENT_LOOP
        # thread_bridge is one path: VST lookup, Elastic sink publishing and
        # dedup state all hand off to the async runtime together. Toggling
        # them individually produced eight variants of a single mode, each
        # with its own fallback behaviour to reason about and test, and every
        # deployment that actually runs thread_bridge enables all three.
        self._warn_retired_scaling_config()
        external_timeout = async_io_cfg.get('external_timeout_seconds', 30)
        try:
            self.async_external_timeout_seconds = max(1.0, float(external_timeout))
        except (TypeError, ValueError):
            self.async_external_timeout_seconds = 30.0
        logger.info("Pipeline mode is %s", self.pipeline_mode)
        logger.info(
            "Async external I/O (VST, Elastic sink, dedup state) is %s "
            "(timeout=%.1fs)",
            "enabled" if self.async_io_enabled else "disabled",
            self.async_external_timeout_seconds,
        )
        # Lazy-initialized VST handler for media path resolution
        self._vst_handler = None
        #TODO add VLM PARAMS INITIALIZATION from config
        self.vlm_client = VLMClient(self.config.get('vlm', {}))
        async_dispatch_workers = self.config.get('alert_agent', {}).get(
            'async_dispatch_workers', self.num_workers
        )
        if not isinstance(async_dispatch_workers, int) or async_dispatch_workers <= 0:
            async_dispatch_workers = self.num_workers
        self.async_dispatch_workers = async_dispatch_workers
        async_dispatch_max_in_flight = self.config.get('alert_agent', {}).get(
            'async_dispatch_max_in_flight',
            self.async_dispatch_workers * 2,
        )
        if not isinstance(async_dispatch_max_in_flight, int) or async_dispatch_max_in_flight <= 0:
            async_dispatch_max_in_flight = self.async_dispatch_workers * 2
        self.async_dispatch_max_in_flight = async_dispatch_max_in_flight
        # Per-service concurrency caps for event_loop mode. Defaulting to the
        # dispatch worker count keeps downstream pressure identical to the
        # thread-bridge profile until operators raise the caps deliberately.
        max_vlm_concurrent = async_io_cfg.get('max_vlm_concurrent', self.async_dispatch_workers)
        if not isinstance(max_vlm_concurrent, int) or max_vlm_concurrent <= 0:
            max_vlm_concurrent = self.async_dispatch_workers
        self.max_vlm_concurrent = max_vlm_concurrent
        max_vst_concurrent = async_io_cfg.get('max_vst_concurrent', self.async_dispatch_workers)
        if not isinstance(max_vst_concurrent, int) or max_vst_concurrent <= 0:
            max_vst_concurrent = self.async_dispatch_workers
        self.max_vst_concurrent = max_vst_concurrent
        self.async_vlm_runtime = (
            AsyncVLMRuntime(
                self.config.get('vlm', {}),
                io_workers=(
                    min(32, max(8, self.max_vst_concurrent + 8))
                    if event_loop_mode
                    else None
                ),
            )
            if self.pipeline_mode != PIPELINE_MODE_SYNC
            else None
        )
        self._vlm_capacity: Optional[asyncio.Semaphore] = (
            asyncio.Semaphore(self.max_vlm_concurrent) if event_loop_mode else None
        )
        self._vst_capacity: Optional[asyncio.Semaphore] = (
            asyncio.Semaphore(self.max_vst_concurrent) if event_loop_mode else None
        )
        if event_loop_mode:
            logger.info(
                "Event-loop concurrency caps: max_in_flight=%s max_vlm_concurrent=%s max_vst_concurrent=%s",
                self.async_dispatch_max_in_flight,
                self.max_vlm_concurrent,
                self.max_vst_concurrent,
            )
        self._message_dispatch_executor: Optional[ThreadPoolExecutor] = None
        self._message_dispatch_lock = threading.Lock()
        self._message_dispatch_futures: Set[Future] = set()
        self._dispatch_backpressure_semaphore: Optional[threading.BoundedSemaphore] = (
            threading.BoundedSemaphore(self.async_dispatch_max_in_flight)
            if self.pipeline_mode != PIPELINE_MODE_SYNC
            else None
        )
        sink_cfg = self.config.get("vlm_enhanced_sink", {}) or {}
        self._vlm_sink_type = (sink_cfg.get("type") or "elastic").lower()
        self._sink_async_lock = threading.Lock()
        self._sink_async_futures: Set[Future] = set()
        async_sink_warn_in_flight = async_io_cfg.get(
            "sink_warn_in_flight",
            self.async_dispatch_max_in_flight,
        )
        if not isinstance(async_sink_warn_in_flight, int) or async_sink_warn_in_flight <= 0:
            async_sink_warn_in_flight = self.async_dispatch_max_in_flight
        self.async_sink_warn_in_flight = async_sink_warn_in_flight
        logger.info(
            "Async sink warning threshold is %s in-flight operations",
            self.async_sink_warn_in_flight,
        )
        if PROMETHEUS_ENABLED:
            ASYNC_SINK_IN_FLIGHT.set(0)
        self._load_custom_parser()
        self._pluggable_parser = self._load_pluggable_parser()
        self._warn_if_parser_configs_collide()
        self.vst_pass_through_mode = self.config.get('alert_agent', {}).get('vst_pass_through_mode', False)
        self._vlm_rate_limit_enabled = bool(self.config.get('vlm_rate_limit_enabled', False))
        self.include_latency_info = self.config.get('alert_agent', {}).get('include_latency_info', False)
        self.url_transform_enabled = self.config.get('alert_agent', {}).get('url_transform', {}).get('enabled', True)

        self.vlm_media_source_using_base64 = self.config.get('vlm', {}).get('vlm_media_source_using_base64', False)

        # Initialize DirectMediaHandler for Mode 3
        self.direct_media_handler = DirectMediaHandler(
            vlm_client=self.vlm_client,
            vlm_enhanced_event_sink=self.vlm_enhanced_event_sink,
            config=self.config,
            pluggable_parser=self._pluggable_parser,
        )

        # Initialize entity validator for request processing
        from schemas import EntityValidator
        self.entity_validator = EntityValidator()

        # Initialize ResponseBuilder for clean response handling
        from schemas.response_entity import ResponseBuilder
        self.response_builder = ResponseBuilder()

        # PromptManager is now initialised earlier (before the VLM
        # enhanced sink build) so its AlertConfigStore can be threaded
        # into the sink for output_category hot-reload. Keeping the log
        # line here for parity with existing operator playbooks.

        # Initialize EnrichmentProcessor
        enrichment_config = self.config.get('alert_agent', {}).get('enrichment', {})
        self.enrichment_processor = EnrichmentProcessor(
            vlm_client=self.vlm_client,
            async_vlm_client=None,
            prompt_manager=self.prompt_manager,
            enabled=enrichment_config.get('enabled', False),
        )

        self._global_vlm_config = dict(self.config.get('vlm', {}))

        # Reuse the cached store already built by PromptManager so the
        # hot-path reads (the alert-config REST API REQ-002) and the file-seed writes
        # share one composite with a common in-memory fallback. Falling
        # back to a second construction would mean two independent
        # hydration runs and, worse, two memory snapshots that could
        # drift under cache-miss repopulation.
        self._alert_config_store = getattr(
            self.prompt_manager, "alert_config_store", None
        )
        if self._alert_config_store is None:
            logger.warning(
                "PromptManager did not expose an alert_config_store; "
                "hot-path per-alert-type overrides will fall back to static config"
            )

        self._openclaw_notifier: "OpenClawNotifier | None" = None
        self._webhook_forwarder: "WebhookKafkaForwarder | None" = None
        _oc_cfg = (self.config.get("webhook") or {}).get("openclaw") or {}
        if _oc_cfg.get("enabled", False):
            from webhook import OpenClawNotifier, WebhookKafkaForwarder

            self._openclaw_notifier = OpenClawNotifier(self.config)
            self._webhook_forwarder = WebhookKafkaForwarder(self.config, self._openclaw_notifier)

    def _load_custom_parser(self):
        """Auto-load custom parser module from vlm.custom_parser_module config."""
        module_path = self.config.get('vlm', {}).get('custom_parser_module')
        if not module_path:
            return

        import importlib
        try:
            importlib.import_module(module_path)
            logger.info("Loaded custom parser module: '%s'", module_path)
        except ImportError as e:
            raise ImportError(
                f"Failed to load custom parser module '{module_path}' "
                f"from vlm.custom_parser_module config: {e}"
            ) from e

    def _load_pluggable_parser(self):
        """Load external pluggable parser from vlm.response_parser config.

        When configured, the parser fully replaces the built-in CR1/CR2
        verification parsing: its ``parse(raw_response) -> dict`` output
        is serialized into ``info["vlm_response"]`` with ``info["verdict"] = None``.
        """
        dotted_path = self.config.get('vlm', {}).get('response_parser')
        if not dotted_path:
            return None
        parser = load_response_parser(dotted_path)
        logger.info("Pluggable response parser active: '%s'", dotted_path)
        return parser

    def _warn_if_parser_configs_collide(self):
        """Warn when both parser-extension mechanisms are configured.

        Alert Bridge exposes two independent parser-extension axes:

        * ``vlm.custom_parser_module`` imports a module whose
          ``register_parser`` side effects populate a *registry* dispatched
          from :func:`VLMResponse.model_validate_text` when
          ``vlm.response_format`` matches a registered name.
        * ``vlm.response_parser`` loads a class whose ``parse()`` method
          *replaces* the built-in parser entirely and writes the raw dict
          into ``info["vlm_response"]`` via the pluggable-parser helpers.

        These are **not** redundant — they operate at different layers,
        so both can be set without one shadowing the other.  However,
        operators frequently configure both expecting one to "win", and
        the resulting behaviour is subtle (the pluggable parser bypasses
        the registry on the default VLM path, but the registry is still
        consulted anywhere ``VLMResponse.model_validate_text`` is
        invoked directly — e.g. in custom downstream paths).

        We surface a single WARN at startup so misconfigurations are
        visible in logs rather than discovered in production.
        """
        module_path = self.config.get('vlm', {}).get('custom_parser_module')
        dotted_path = self.config.get('vlm', {}).get('response_parser')
        if module_path and dotted_path:
            logger.warning(
                "Both vlm.custom_parser_module=%r and vlm.response_parser=%r "
                "are configured. These mechanisms operate at different layers "
                "and are not mutually exclusive, but this combination is "
                "uncommon and usually indicates a misconfiguration. "
                "Precedence on the default VLM path: vlm.response_parser "
                "(pluggable) replaces the built-in parser entirely and "
                "bypasses the custom_parser_module registry; "
                "custom_parser_module is still active for any path that "
                "calls VLMResponse.model_validate_text directly. If you "
                "intend to use only the pluggable parser, remove "
                "vlm.custom_parser_module.",
                module_path,
                dotted_path,
            )

    @staticmethod
    def load_config(config_file):
        # Security: Validate the config file path against an allowlisted base directory
        from pathlib import Path

        try:
            # Determine allowlisted base directory
            base_dir = Path(os.getenv("ALERT_AGENT_CONFIG_DIR", Path(__file__).parent)).resolve()

            # Resolve candidate path strictly (ensures existence and resolves symlinks)
            resolved_path = Path(config_file).resolve(strict=True)

            # Only allow YAML files
            if resolved_path.suffix.lower() not in ['.yaml', '.yml']:
                raise ValueError(f"Config file must be a YAML file: {config_file}")

            # Enforce that the resolved path is inside the allowlisted base directory
            try:
                # Python 3.9+: raises ValueError if not relative
                resolved_path.relative_to(base_dir)
            except Exception:
                raise ValueError(f"Config path not allowed: {resolved_path}")

            # Read the file
            with resolved_path.open('r') as file:
                return yaml.safe_load(file)

        except FileNotFoundError:
            raise FileNotFoundError(f"Config file not found: {config_file}")
        except Exception as e:
            raise ValueError(f"Error loading config file {config_file}: {e}")

    def _get_merged_vlm_config(self, category: str) -> dict:
        """Return global VLM config merged with per-alert-type vlm_params overrides.

        Precedence (highest wins):
          1. Runtime API config from Redis (alert_config:{category}.vlm_params)
          2. Static file config (alert_type_config.json vlm_params)
          3. Global VLM config (config.yaml vlm section)
        """
        merged = dict(self._global_vlm_config)

        if self.prompt_manager.alert_config_loader:
            file_params = self.prompt_manager.alert_config_loader.get_vlm_params_for_alert_type(category)
            if file_params:
                merged.update(file_params.model_dump(exclude_none=True))

        if self._alert_config_store is not None:
            try:
                redis_config = self._alert_config_store.get(category)
                if redis_config and redis_config.get('vlm_params'):
                    merged.update({
                        k: v for k, v in redis_config['vlm_params'].items()
                        if v is not None
                    })
            except Exception:
                pass

        return merged

    def validate_video_url(self, url: str, timeout: int = 10, max_retries: int = 8, retry_delay: float = 0.05) -> bool:
        """
        Validate if a video URL is accessible with retry logic for race conditions.
        Makes a lightweight HEAD-like check using streaming GET to verify URL is accessible.
        Retries multiple times to handle cases where video file is still being written.

        Args:
            url: The video URL to validate
            timeout: Timeout in seconds for each request (default: 10)
            max_retries: Maximum number of validation attempts (default: 5)
            retry_delay: Delay in seconds between retries (default: 0.5)

        Returns:
            bool: True if URL returns 200 OK with content-length > 0, False otherwise
        """
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.debug(f"Retrying validation (attempt {attempt + 1}/{max_retries}) after {retry_delay}s delay")
                    time.sleep(retry_delay)
                else:
                    logger.debug(f"Validating video URL: {url}")

                # Use streaming GET but immediately close to just check headers
                # This is essentially a HEAD request that works even if server doesn't support HEAD
                response = requests.get(url, stream=True, timeout=timeout, allow_redirects=True)

                try:
                    content_type = response.headers.get("content-type", "").lower()
                    content_length = response.headers.get("content-length", "0")
                    status_code = response.status_code

                    logger.debug(
                        f"URL validation - Status: {status_code}, Content-Type: {content_type}, Content-Length: {content_length} bytes"
                    )

                    # Check status is OK
                    if not (200 <= status_code < 300):
                        logger.warning(f"URL validation failed - HTTP Status: {status_code}")
                        if attempt < max_retries - 1:
                            continue
                        else:
                            logger.error(f"URL validation failed after {max_retries} attempts - final status: {status_code}")
                            return False

                    # Check content-length indicates file exists
                    try:
                        length = int(content_length)
                        if length == 0:
                            logger.warning("URL validation failed - Content-Length is 0, video file may not be ready")
                            if attempt < max_retries - 1:
                                continue
                            else:
                                logger.error(f"URL validation failed after {max_retries} attempts - Content-Length still 0")
                                return False
                        if length < 1000:
                            logger.warning(f"URL has suspiciously small content-length: {length} bytes")
                            # Don't fail, might be a very short video
                    except ValueError:
                        logger.warning("Could not parse Content-Length header")
                        # Don't fail on missing content-length, some servers don't send it

                    logger.info(f"URL validation successful on attempt {attempt + 1}")
                    return True

                finally:
                    response.close()  # Always close the connection

            except requests.RequestException as e:
                logger.warning(f"Request error validating URL (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    logger.error(f"URL validation failed after {max_retries} attempts due to request errors")
                    return False
            except Exception as e:
                logger.error(f"Unexpected error validating URL: {e}")
                return False

        return False

    def _apply_vlm_rate_limit(self, dedup_filtered: list[dict]) -> list[dict]:

        verify_only_finished = self.config.get('alert_agent', {}).get('verify_only_finished_events', False)

        if not self._vlm_rate_limit_enabled:
            #logger.debug("VLM rate limiting disabled; using dedup-filtered messages")
            return dedup_filtered

        try:
            rate_limit_filtered = self._run_redis_operation_with_mode(
                "filter_new_events_rate_limit",
                self.redis_handler.filter_new_events,
                dedup_filtered,
                rate_limit=True,
                verify_only_finished_events=verify_only_finished,
            )
        except Exception as exc:
            logger.error("VLM rate limit check failed; allowing messages", extra={"error": str(exc)})
            return dedup_filtered

        dropped = len(dedup_filtered) - len(rate_limit_filtered)
        # Identity diff is safe: the rate-limit filter returns the same dict
        # references for kept messages (no copy). Any future filter refactor
        # that copies dicts must update _dropped_messages accordingly.
        inc_events_dropped(
            "rate_limit",
            dropped,
            messages=_dropped_messages(dedup_filtered, rate_limit_filtered),
        )
        if dropped:
            logger.info(
                "VLM rate limit dropped %s messages (kept %s of %s)",
                dropped,
                len(rate_limit_filtered),
                len(dedup_filtered),
            )

        return rate_limit_filtered

    def _warn_retired_scaling_config(self) -> None:
        """Report configuration that no longer has any effect.

        Retired keys are ignored rather than rejected so an existing
        deployment still boots; the warning is what tells the operator their
        tuning is not doing what they think it is.
        """
        alert_agent_cfg = self.config.get('alert_agent', {}) or {}
        async_io_cfg = alert_agent_cfg.get('async_io', {}) or {}

        retired = sorted(
            f"alert_agent.async_io.{key}"
            for key in ('vst_enabled', 'elastic_enabled', 'dedup_enabled', 'redis_enabled')
            if key in async_io_cfg
        )
        if 'chunk_size' in alert_agent_cfg:
            # Dispatch is per message in every mode, so grouping messages only
            # ever changed the granularity of the scheduling loop.
            retired.append("alert_agent.chunk_size")
        if retired:
            logger.warning(
                "Ignoring retired configuration: %s. Scaling is tuned through "
                "processes, pipeline_mode, num_workers, async_dispatch_workers "
                "and async_io.max_vlm_concurrent.",
                ", ".join(retired),
            )

        if 'enabled' in async_io_cfg:
            logger.warning(
                "alert_agent.async_io.enabled is deprecated; set "
                "alert_agent.pipeline_mode instead. It is consulted only when "
                "pipeline_mode is unset."
            )

    def _schedule_message(
        self,
        worker_pool: Optional[ThreadPoolExecutor],
        message: Dict[str, Any],
        message_type: str,
        batch: Dict[str, Any],
    ) -> None:
        """Hand one message to the mode's executor, or run it inline.

        ``worker_assigned_at`` is stamped at the moment the message is
        accepted for processing, so ``WORKER_QUEUE_WAIT_DURATION`` keeps one
        meaning in every mode: "kafka_consumed -> accepted for processing".
        """
        # Taken before the pool, not inside it: a message queued for a worker
        # is already this instance's responsibility, and counting it only once
        # it starts let a drain finish while queued records still waited.
        #
        # Not at read time either, though that would also cover records a
        # mid-batch revoke stranded. The rebalance callback is delivered by the
        # poll that fills the batch, and every release path runs only after that
        # poll returns -- so the drain would wait on work that the very thread
        # it blocks is the only one able to release, and spend its whole budget.
        admission = self._partition_in_flight.accept(_batch_partition(batch))

        if worker_pool is None:
            # Async modes dispatch each message onward and return, so there is
            # nothing to run on a separate thread. Backpressure still applies:
            # the dispatch semaphore blocks inside ``process_batch_vlm``, which
            # stalls this consume loop exactly as the worker queue used to.
            # ``process_batch_vlm`` swallows its own exceptions, so running it
            # here cannot break the loop.
            self.process_batch_vlm(
                0,
                [message],
                message_type,
                batch.get("kafka_consumed_at"),
                batch.get("kafka_published_at"),
                datetime.now(timezone.utc).isoformat(),
                admission=admission,
            )
            return

        worker_id = None
        while worker_id is None:
            try:
                worker_id = self.worker_queue.get(timeout=5)
            except Empty:
                logger.debug("All workers busy. Waiting to schedule next message...")

        try:
            future: Future = worker_pool.submit(
                partial(self.process_batch_vlm, admission=admission),
                worker_id,
                [message],
                message_type,
                batch.get("kafka_consumed_at"),
                batch.get("kafka_published_at"),
                datetime.now(timezone.utc).isoformat(),
            )
        except Exception:
            # A shutdown between reading and scheduling rejects the submit.
            # Only process_batch_vlm releases, and it will never run, so the
            # count would stay owed for the life of the process.
            if admission is not None:
                admission.release()
            self.worker_queue.put(worker_id)
            raise
        future.add_done_callback(
            lambda _f, released_id=worker_id: self.worker_queue.put(released_id)
        )

    def _publish_assignment_state(self) -> None:
        """Republish what this process holds, and reopen the drain budget.

        Runs whenever an assignment is decided or taken away, in every
        deployment shape. The budget is closed for the rebalance that is over,
        so the next one starts with the whole of it.
        """
        from metrics.recorder import (
            set_assigned_partitions, set_pipeline_process_counts,
        )

        ready = self.source.is_ready()
        set_assigned_partitions(self.source.assigned_partition_count())
        if ready:
            self._rebalance_drain_deadline = None
        if self._publishes_own_fleet_state:
            fleet_state.publish(1, 1, 1 if ready else 0)
            # A lone pipeline is its own fleet. The counts were published only
            # by the supervisor, so at one process they were never written at
            # all and /ready read "nothing configured" as nothing wrong --
            # answering 200 through startup and through every rebalance.
            set_pipeline_process_counts(1, 1, 1 if ready else 0)

    def _drain_revoked_partitions(self, partitions) -> None:
        """Finish what is owed on partitions about to move to another member.

        Runs inside the rebalance callback, on the consume thread, so the
        group cannot complete the rebalance until this returns. It is bounded
        for that reason: overrunning the poll interval would cost this member
        its place and start another rebalance, which is worse than the overlap
        it is trying to avoid.

        This is about overlap, not durability. Offsets are committed at read,
        so a process that is killed loses whatever it held regardless.
        """
        from metrics.recorder import inc_rebalance_drain

        if self._rebalance_drain_deadline is None:
            # Opened by the first revoke of a rebalance and closed when the
            # coordinator answers again. Not reopened on expiry: a spent
            # budget means this rebalance has had its allowance, and handing
            # the next consumer a fresh one is the per-consumer bound this
            # replaces.
            self._rebalance_drain_deadline = time.monotonic() + DEFAULT_DRAIN_TIMEOUT

        if not partitions:
            return
        outstanding = sum(self._partition_in_flight.in_flight(p) for p in partitions)
        # Records already read for these partitions but not yet handed to a
        # stage. The drain cannot wait for them -- the thread it would block
        # is the one that has to return them -- but reporting nothing_owed
        # over them told an operator the rebalance was clean while the other
        # counter was recording a stranding for the same moment.
        stranded = self.source.buffered_for(partitions)
        if stranded:
            # Correlated here, at the moment it happens. The two counters tell
            # one story between them and an operator reading only the drain
            # outcome would see a clean rebalance: the drain did finish what it
            # could, and these were read too late for it to know about.
            logger.warning(
                "%d record(s) were already read for %d revoked partition(s) and "
                "cannot be drained; this member finishes them while the incoming "
                "owner starts. See alert_bridge_records_read_after_revoke_total.",
                stranded, len(partitions),
            )
        if not outstanding:
            inc_rebalance_drain("nothing_owed")
            return
        logger.info(
            "Draining %d message(s) still in flight across %d revoked partition(s)",
            outstanding, len(partitions),
        )
        remaining = max(0.0, self._rebalance_drain_deadline - time.monotonic())
        drained = self._partition_in_flight.drain(partitions, timeout=remaining)
        inc_rebalance_drain("drained" if drained else "timed_out")

    def _needs_worker_pool(self) -> bool:
        """Whether the batch pool between the consume loop and processing is useful.

        It belongs to sync mode, where per-message processing blocks and thread
        count is the only source of parallelism. Both async modes hand each
        message to their own dispatcher and return immediately, so the pool
        would cost a thread hop and a second queue without raising any
        concurrency limit.

        Pass-through is the exception in every mode: process_batch_vlm returns
        through _process_media_passthrough before reaching a dispatcher and
        makes its VLM calls inline, so without a pool that work runs one
        message at a time on the consume thread and stalls polling behind it.
        """
        return self.pipeline_mode == PIPELINE_MODE_SYNC or self.vst_pass_through_mode

    def _run_consume_loop(self, worker_pool: Optional[ThreadPoolExecutor]) -> None:
        """Poll the source and schedule every message until interrupted."""
        while True:
            raw_messages = self.source.read_data()

            if self._webhook_forwarder is not None:
                self._webhook_forwarder.poll_and_forward()

            if not raw_messages:
                continue

            # Batches already normalized by source: [{'kind','messages'}, ...]
            for batch in raw_messages:
                batch_messages = batch.get("messages")
                if not batch_messages:
                    continue

                batch_kind = (batch.get("kind") or "").lower()
                message_type = "Incident" if batch_kind == "incident" else "Behavior"

                for message in batch_messages:
                    self._schedule_message(worker_pool, message, message_type, batch)

    def process_anomalies(self) -> bool:
        """Run the consume loop until it stops. False if it stopped on an error.

        The caller decides what that means: a supervised child already fails
        its instance by exiting, but a single-process run has to turn it into
        a non-zero exit itself or an orchestrator sees a clean stop.
        """
        dispatch_executor: Optional[ThreadPoolExecutor] = None
        worker_pool: Optional[ThreadPoolExecutor] = None
        failed = False
        try:
            if self.pipeline_mode == PIPELINE_MODE_THREAD_BRIDGE:
                dispatch_executor = ThreadPoolExecutor(
                    max_workers=self.async_dispatch_workers,
                    thread_name_prefix="ab-vlm-dispatch",
                )
                self._message_dispatch_executor = dispatch_executor
                logger.info(
                    "Async message dispatch enabled with %s workers",
                    self.async_dispatch_workers,
                )
            elif self.pipeline_mode == PIPELINE_MODE_EVENT_LOOP:
                logger.info(
                    "Event-loop message dispatch enabled (max_in_flight=%s, "
                    "max_vlm_concurrent=%s, max_vst_concurrent=%s)",
                    self.async_dispatch_max_in_flight,
                    self.max_vlm_concurrent,
                    self.max_vst_concurrent,
                )

            if self._needs_worker_pool():
                worker_pool = ThreadPoolExecutor(
                    max_workers=self.num_workers,
                    thread_name_prefix="ab-vlm-worker",
                )
                for worker_id in range(self.num_workers):
                    self.worker_queue.put(worker_id)

            self._run_consume_loop(worker_pool)

        except KeyboardInterrupt:
            logger.info("Process interrupted by user, shutting down gracefully")
        except Exception as e:
            # Recorded, not swallowed. The loop is over either way, but under a
            # supervisor the child's exit already fails the instance while a
            # lone pipeline returned here and let the process exit 0 -- so the
            # default deployment could stop consuming and report success.
            failed = True
            logger.error("Error during anomaly processing", extra={
                "error": str(e),
                "error_type": type(e).__name__
            }, exc_info=True)
        finally:
            # Drain the worker pool before the dispatch pool: sync-mode workers
            # can still be feeding it.
            if worker_pool is not None:
                worker_pool.shutdown(wait=True)
            if dispatch_executor is not None:
                dispatch_executor.shutdown(wait=True)
            self._message_dispatch_executor = None
            with self._message_dispatch_lock:
                in_flight_futures = list(self._message_dispatch_futures)
            if in_flight_futures:
                logger.info(
                    "Waiting for in-flight dispatch tasks before shutdown",
                    extra={"in_flight": len(in_flight_futures)},
                )
            # One deadline for the whole drain, not thirty seconds per task.
            # The supervisor terminates survivors a few seconds after the
            # drain window closes, so a per-task wait meant long work was
            # killed rather than finished or cancelled -- the timeline says
            # cancel and close, and this could not reach either.
            drain_until = time.monotonic() + DRAIN_SECONDS
            for in_flight_future in in_flight_futures:
                try:
                    in_flight_future.result(
                        timeout=max(0.0, drain_until - time.monotonic())
                    )
                except FutureTimeoutError:
                    logger.warning("Timed out waiting for in-flight dispatch task during shutdown")
                except Exception:
                    logger.exception("In-flight dispatch task failed during shutdown")
            with self._message_dispatch_lock:
                self._message_dispatch_futures.clear()
            with self._sink_async_lock:
                sink_futures = list(self._sink_async_futures)
            if sink_futures:
                logger.info(
                    "Waiting for in-flight async sink operations before shutdown",
                    extra={"in_flight": len(sink_futures)},
                )
            for sink_future in sink_futures:
                try:
                    # Shares the same window as the dispatch drain above:
                    # both run inside the one the supervisor allows.
                    sink_future.result(
                        timeout=max(0.0, min(self.async_external_timeout_seconds,
                                             drain_until - time.monotonic()))
                    )
                except FutureTimeoutError:
                    logger.warning("Timed out waiting for async sink operation during shutdown")
                except Exception:
                    logger.exception("Async sink operation failed during shutdown")
            with self._sink_async_lock:
                self._sink_async_futures.clear()
            if PROMETHEUS_ENABLED:
                ASYNC_SINK_IN_FLIGHT.set(0)
            if (
                self.async_vlm_runtime is not None
                and self.pipeline_mode == PIPELINE_MODE_EVENT_LOOP
            ):
                # Close async clients in order (HTTP -> Elastic) before the
                # runtime closes the VLM client and stops the loop.
                #
                # Skipped on a runtime that never ran: the runtime is built
                # lazily, so submitting here would start the loop thread and
                # a fresh client in order to close clients that were never
                # opened, and stop() would then tear it all down again.
                #
                # Bounded, unlike run_coroutine, which waits on the future
                # forever -- a client whose aclose() never returned held the
                # whole shutdown past any container grace. The deadline covers
                # the submit as well as the wait, because submit_coroutine
                # starts the runtime if it is not up and that start has its
                # own ten-second wait: timing only the result would have made
                # a step documented as five seconds take fifteen.
                if self.async_vlm_runtime.is_running():
                    close_deadline = time.monotonic() + CLIENT_CLOSE_TIMEOUT
                    try:
                        self.async_vlm_runtime.submit_coroutine(
                            self._aclose_event_loop_clients()
                        ).result(timeout=max(0.0, close_deadline - time.monotonic()))
                    except Exception:
                        logger.exception("Failed closing event-loop clients during shutdown")
            if self.async_vlm_runtime is not None:
                self.async_vlm_runtime.stop()
            if self._webhook_forwarder is not None:
                self._webhook_forwarder.close()
            if self._openclaw_notifier is not None:
                self._openclaw_notifier.close()
            self.sink.close()
            # Closing the consumer leaves the group, which delivers a revoke
            # on this thread. That revoke would otherwise find no open budget
            # -- the last decided assignment cleared it -- and mint a fresh
            # DEFAULT_DRAIN_TIMEOUT, a second full drain after the one above
            # already ran. Handing it the drain's own deadline gives it
            # whatever is left of that one window: the full budget if nothing
            # was in flight, nothing at all if the drain used it up. Either
            # way shutdown cannot spend its allowance twice.
            self._rebalance_drain_deadline = drain_until
            self.source.close()
            logger.info("Resources closed successfully")

        return not failed

    def set_max_frames(self, start_time: str, end_time: str) -> int:
        """
        Set the maximum number of frames to process based on the duration of the video.
        """

        start_time = parse_iso_utc(start_time)
        end_time = parse_iso_utc(end_time)
        duration = end_time - start_time
        if duration.total_seconds() <= 1:
            return self.vlm_client.config.get('num_frames', 10)
        if duration.total_seconds() >= 30:
            return 72
        else:
            return int(0.281*duration.total_seconds()+9.7183)

    def process_batch_vlm(
        self,
        worker_id,
        messages,
        message_type=None,
        kafka_consumed_at=None,
        kafka_published_at=None,
        worker_assigned_at=None,
        admission=None,
    ):
        """
        Processes a batch of messages from the event bridge source.
        :param worker_id: ID of the worker processing the batch.
        :param messages: List of simple JSON messages.
        :param message_type: Optional protobuf message type (e.g., 'Incident' or 'Behavior')
        :param kafka_consumed_at: ISO timestamp when batch was consumed from Kafka
        :param kafka_published_at: ISO timestamp when message was published to Kafka (producer timestamp)
        :param worker_assigned_at: ISO timestamp when the batch scheduler
            dequeued this batch from the worker queue. Stamped at the
            outermost queue exit so ``WORKER_QUEUE_WAIT_DURATION`` has
            consistent semantics across sync and async-dispatch modes
            (C24). If ``None`` (older callers or the VSS path that
            does not surface the stamp), ``_process_single_message``
            falls back to stamping at its own entry.
        """

        video_url = None
        # Every admission this frame owns. The batch arrives with one; a batch
        # carrying several messages takes one more per message, because a
        # single admission is released by whichever message finishes first and
        # a drain would then pass over the rest while they were still running.
        admissions = [admission]
        try:
            # logger.info("Processing batch of size %s", len(messages), extra={
            #     "worker_id": worker_id,
            #     "batch_size": len(messages)
            # })

            if not messages:
                logger.debug("Empty batch received", extra={"worker_id": worker_id})
                return

            if not message_type:
                raise ValueError("message_type is required for process_batch_vlm")
            if isinstance(messages, list) and all(isinstance(m, dict) for m in messages):
                parsed_messages = messages
            elif isinstance(messages, list) and all(isinstance(m, str) for m in messages):
                parsed_messages = []
                for raw in messages:
                    try:
                        parsed_messages.append(json.loads(raw))
                    except (json.JSONDecodeError, TypeError) as exc:
                        logger.warning(
                            "Skipping malformed JSON message in batch: %s", exc
                        )
            else:
                # Kafka sources provide protobuf tuples; Redis Stream sources
                # provide JSON strings. Only run protobuf decoding for the
                # Kafka-shaped tuple path.
                messages_input = messages if isinstance(messages, dict) else {'batch': messages}
                decoded_messages = protobuf_anomalies_to_json_string_list(
                    messages_input,
                    message_type
                )
                parsed_messages = []
                for message in decoded_messages:
                    if isinstance(message, str):
                        try:
                            parsed_messages.append(json.loads(message))
                        except (json.JSONDecodeError, TypeError) as exc:
                            logger.warning(
                                "Skipping malformed JSON message in batch: %s", exc
                            )
                    elif isinstance(message, dict):
                        parsed_messages.append(message)

            messages = parsed_messages

            # Normalize alerts Msg
            messages = (
                [normalize_alert_message(m) for m in messages]
                if (message_type or "").lower() != "incident"
                else messages
            )

            if self.vst_pass_through_mode:
                self._process_media_passthrough(worker_id, messages)
                return

            # VLM deduplication: filter duplicates before validation
            if self.redis_handler is not None:
                # Filter 2: End time delta (record time) - runs first
                pre_end_time_delta = messages
                messages = self._run_redis_operation_with_mode(
                    "filter_by_end_time_delta",
                    self.redis_handler.filter_by_end_time_delta,
                    messages,
                )
                # C21: when the per-sensor flag is on, we need the
                # actually-dropped messages (not just the count) to
                # break the counter down by sensor. ``_dropped_messages``
                # computes the set difference by object identity, which
                # is safe because these are the same dict objects the
                # filter returned to us. Any future filter that copies
                # dicts must update _dropped_messages accordingly.
                inc_events_dropped(
                    "end_time_delta",
                    len(pre_end_time_delta) - len(messages),
                    messages=_dropped_messages(pre_end_time_delta, messages),
                )
                if not messages:
                    logger.debug("All messages dropped by end time delta filter; nothing to process")
                    return

                # Filter 1: Existing dedup (system time TTL)
                verify_only_finished = self.config.get('alert_agent', {}).get('verify_only_finished_events', False)
                pre_dedup = messages
                dedup_filtered = self._run_redis_operation_with_mode(
                    "filter_new_events_dedup",
                    self.redis_handler.filter_new_events,
                    messages,
                    verify_only_finished_events=verify_only_finished,
                )
                # NOTE: the previous implementation computed this as
                # ``len(parsed_messages) - len(dedup_filtered)``, which
                # conflated end-time-delta drops with dedup drops and
                # overstated the "dedup" bucket. Using the pre-dedup count
                # directly scopes the counter to the dedup filter only and
                # lets ``EVENTS_DROPPED{reason="dedup"}`` be interpreted
                # as "Redis TTL collisions" in isolation.
                # Identity diff is safe: dedup filter returns the same dict
                # references for kept messages (no copy).
                inc_events_dropped(
                    "dedup",
                    len(pre_dedup) - len(dedup_filtered),
                    messages=_dropped_messages(pre_dedup, dedup_filtered),
                )

                if not dedup_filtered:
                    logger.debug("All messages dropped by VLM dedup; nothing to process")
                    return

                messages = self._apply_vlm_rate_limit(dedup_filtered)
                if not messages:
                    logger.debug("All messages dropped by VLM rate limit; nothing to process")
                    return

            if self._vst_handler is None:
                try:
                    from vst.its_vst_handler import ITS_VST_HANDLER
                    self._vst_handler = ITS_VST_HANDLER(self.config)
                except Exception as init_err:
                    logger.error("Failed to initialize VST handler", extra={
                        "error": str(init_err)
                    }, exc_info=True)

            total_messages = len(messages)
            inc_events_after_dedup(total_messages, messages=messages)
            # Taken before the loop, not as each message is reached: message 1
            # is handed to a dispatcher that releases on another thread, so a
            # split taken afterwards could admit against a partition whose
            # count had already fallen to zero.
            admissions.extend(
                admission.split() if admission is not None else None
                for _ in messages[1:]
            )
            for idx, message in enumerate(messages, start=1):
                message_admission = admission if idx == 1 else admissions[idx - 1]
                event_type = 'alert' if is_alert(message) else 'incident'
                if self.async_io_enabled or getattr(self, 'pipeline_mode', None) == PIPELINE_MODE_EVENT_LOOP:
                    logger.debug(f"Queueing {event_type} message {idx}/{total_messages} for async dispatch")
                else:
                    logger.debug(f"Processing {event_type} message {idx}/{total_messages}")
                self._process_single_message_with_mode(
                    worker_id,
                    message,
                    kafka_consumed_at,
                    kafka_published_at,
                    worker_assigned_at=worker_assigned_at,
                    admission=message_admission,
                )

        except Exception as e:
            logger.error("Error processing batch", extra={
                "worker_id": worker_id,
                "error": str(e),
                "error_type": type(e).__name__
            }, exc_info=True)
            return
        finally:
            # Every way out of this frame that did not hand the message to a
            # dispatcher ends the message here -- dropped by dedup or the rate
            # limiter, filtered, or finished inline. The dispatched case marks
            # the admission transferred and its completion callback releases
            # instead, so a drain waits for exactly the work still outstanding.
            for held in admissions:
                if held is not None and not held.transferred:
                    held.release()

    def _process_media_passthrough(self, worker_id: int, messages: List[Dict[str, Any]]) -> None:
        """
        Extended pass-through mode with support for:
        - Mode 2: Local file (info.video_path)
        - Mode 3: Direct media URL (info.media_url)

        Routing priority: media_url > video_path > skip
        """
        for message in messages:
            # Skip alerts in pass-through mode; only process incidents
            if isinstance(message, dict) and message.get('notification_type') == 'alert':
                logger.debug("Pass-through mode: skipping alert message", extra={
                    "worker_id": worker_id,
                    "message_id": message.get('id')
                })
                continue

            try:
                user_prompt, system_prompt = self.prompt_manager.get_prompts_for_message(message)

                if os.getenv('LOG_VERBOSE_PROMPTS', 'false').lower() in ('1', 'true', 'yes'):
                    logger.debug(f"User Prompt: {user_prompt}\nSystem Prompt: {system_prompt}")

                info_block = message.get('info') or {}
                category = message.get('category', '')
                merged_vlm = self._get_merged_vlm_config(category)

                # ROUTING: Check for direct media URLs
                # Handle both list and JSON string
                media_urls = info_block.get('media_urls')
                if isinstance(media_urls, str):
                    try:
                        media_urls = json.loads(media_urls)
                    except json.JSONDecodeError:
                        media_urls = None

                if media_urls and isinstance(media_urls, list) and len(media_urls) > 0 and self.direct_media_handler.enabled:
                    logger.info("Mode 3: Direct media URLs detected (%d), bypassing VST", len(media_urls), extra={
                        "worker_id": worker_id,
                        "message_id": message.get('id'),
                    })
                    self.direct_media_handler.evaluate(
                        worker_id=worker_id,
                        message=message,
                        info_block=info_block,
                        user_prompt=user_prompt,
                        system_prompt=system_prompt,
                        config_overrides=merged_vlm,
                    )
                    continue

                # Local file path
                video_path = info_block.get('video_path') or message.get('videoPath')
                if video_path:
                    self._evaluate_local_video(
                        worker_id=worker_id,
                        message=message,
                        video_path=video_path,
                        user_prompt=user_prompt,
                        system_prompt=system_prompt,
                        config_overrides=merged_vlm,
                    )
                    continue

                # No media source found
                logger.warning("Pass-through mode: no media source found (media_urls or video_path)", extra={
                    "worker_id": worker_id,
                    "message_id": message.get('id')
                })

            except Exception as err:
                logger.error("Pass-through mode: failed to process message", extra={
                    "worker_id": worker_id,
                    "message_id": message.get('id'),
                    "error": str(err),
                    "error_type": type(err).__name__
                }, exc_info=True)

    def _evaluate_local_video(
        self,
        worker_id: int,
        message: Dict[str, Any],
        video_path: str,
        user_prompt: str,
        system_prompt: str,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Evaluate a local video file through the VLM and merge the response.

        If a pluggable parser is configured it replaces the default verification
        parsing; its output is JSON-stringified into ``info["vlm_response"]`` with
        ``info["verdict"] = None``. Otherwise the default CR1/CR2 verification
        path runs unchanged.
        """
        if not os.path.isfile(video_path):
            logger.warning("Pass-through mode: video file not found; skipping message", extra={
                "worker_id": worker_id,
                "message_id": message.get('id'),
                "video_path": video_path
            })
            return

        logger.info("VLM request sent (local video)")

        vlm_response: ChatCompletionMessage = self.vlm_client.analyze_local_video(
            video_path,
            user_prompt,
            system_prompt,
            config_overrides=config_overrides,
        )
        response_content = vlm_response.content
        if os.getenv('LOG_VERBOSE_VLM_RESPONSE', 'false').lower() in ('1', 'true', 'yes'):
            logger.debug(f"Raw VLM response: {response_content}")
        verification_successful = False
        if self._pluggable_parser is not None:
            try:
                parsed = self._pluggable_parser.parse(response_content)
                if not isinstance(parsed, dict):
                    raise TypeError(
                        f"Pluggable parser returned {type(parsed).__name__}, expected dict"
                    )
            except Exception as parser_error:
                _apply_pluggable_parser_error(
                    message, parser_error, video_source=video_path,
                )
                # Parser crashes are operational errors — route to
                # _publish_error_with_mode (mirrors the VST path's
                # contract: response_content is not None → success,
                # else → error). Default-path schema errors below are
                # out of scope for this MR and retain pre-existing
                # behavior.
                response_content = None
            else:
                _apply_pluggable_parser_output(
                    message, parsed, video_source=video_path,
                )
                verification_successful = True
        else:
            try:
                # Resolve parser config the same way VLM-request config is
                # resolved: per-category ``config_overrides`` (from
                # ``_get_merged_vlm_config``) win over the global ``vlm``
                # block. Previously we read ``self.config.get('vlm', {})``
                # unconditionally, so per-category overrides for
                # ``model`` / ``response_format`` / ``json_parser``
                # affected the *request* but not the *response parser*
                # (parser uses config_overrides on the local-file path).
                effective_vlm_cfg = {
                    **self.config.get('vlm', {}),
                    **(config_overrides or {}),
                }
                vlm_data = VLMResponse.model_validate_text(
                    response_content,
                    model_name=effective_vlm_cfg.get('model', ''),
                    response_format=effective_vlm_cfg.get('response_format', 'auto'),
                    json_config=effective_vlm_cfg.get('json_parser'),
                )
                merge_info_with_response(
                    message,
                    AlertBridgeResponse(
                        vlm_response=vlm_data,
                        video_source=video_path,
                        verification_response_code=200,
                        verification_response_status="OK",
                    ),
                )
                verification_successful = True
            except Exception as e:
                logger.warning(
                    "VLM response parsing failed",
                    extra={
                        "id": message.get('id'),
                        "sensorId": message.get('sensorId'),
                        "error": str(e),
                    },
                )
                merge_info_with_response(
                    message,
                    AlertBridgeResponse(
                        vlm_response=None,
                        video_source=video_path,
                        verification_response_code=500,
                        verification_response_status="Incorrect VLM response schema",
                        verdict="verification-failed",
                        error_source=ERROR_SOURCE_VLM_SCHEMA,
                    ),
                )
        # Publish routing mirrors the VST path: success when the VLM
        # produced a response we could process (``response_content is
        # not None``); pluggable-parser crashes clear it above and
        # therefore fall through to ``_publish_error_with_mode``.
        if response_content is not None:
            publish_future = self._publish_success_with_mode(
                message,
                user_prompt,
                system_prompt,
                response_content,
            )
        else:
            publish_future = self._publish_error_with_mode(
                message,
                user_prompt,
                system_prompt,
                {},
            )

        # Process enrichment after publish
        if verification_successful:
            category = message.get('category', '')
            enrichment_result = self._process_enrichment_with_mode(
                message=message,
                video_url=video_path,
                system_prompt=system_prompt,
                sensor_id=message.get('sensorId', 'N/A'),
                config_overrides=self._get_merged_vlm_config(category),
            )
            if enrichment_result:
                self.enrichment_processor.merge_into_message(message, enrichment_result)
                self._update_enrichment_with_mode(
                    message,
                    enrichment_result,
                    publish_future=publish_future,
                )



    def _process_single_message(
        self,
        worker_id: int,
        message: Dict[str, Any],
        kafka_consumed_at: str = None,
        kafka_published_at: str = None,
        worker_assigned_at: str = None,
    ) -> None:
        worker_start_time = time.time()
        # C24: prefer the timestamp stamped by the batch scheduler
        # (when the sub-batch was dequeued from the worker queue). This
        # makes ``WORKER_QUEUE_WAIT_DURATION`` semantically consistent
        # between sync and async-dispatch modes — both measure the
        # outermost queue-exit, not the dispatch-executor pickup.
        # Fall back to an inline stamp for callers (e.g. VSS path or
        # test harnesses) that do not surface the batch-level value.
        if worker_assigned_at is None:
            worker_assigned_at = datetime.now(timezone.utc).isoformat()
        sensor_id = message.get('sensorId')

        # C25: initialize ``latency`` up-front so the pre-VST early-exit
        # handler (below) has a valid dict to hand to
        # ``record_event_complete`` when the skip check raises. Before
        # C25, any Redis failure inside ``_set_message_id_and_should_skip``
        # bubbled out of this function with no metric attached —
        # events silently vanished from ``EVENTS_TOTAL`` during Redis
        # incidents and operators had no dashboard correlate.
        latency = {
            'timestamps': {
                'kafkaPublishedAt': kafka_published_at,
                'kafkaConsumedAt': kafka_consumed_at,
                'workerAssignedAt': worker_assigned_at,
            },
        }

        prompts = self._prepare_message_context(
            message, sensor_id, latency, worker_start_time
        )
        if prompts is None:
            return
        user_prompt, system_prompt = prompts

        video_url = None
        storage_video_url = None
        try:
            video_url, effective_start_time, effective_end_time, vst_error_captured = (
                self._resolve_video_url(message, sensor_id, latency)
            )

            if not video_url:
                self._handle_media_collection_failure(
                    message, vst_error_captured, worker_start_time, latency
                )
                return

            vlm_video_url, storage_video_url = self._transform_video_urls(video_url)

            if not self.validate_video_url(video_url):
                self._handle_url_validation_failure(
                    message, storage_video_url, worker_start_time, latency
                )
                return

            category = message.get('category', '')
            merged_vlm = self._get_merged_vlm_config(category)

            if merged_vlm.get('dynamic_frame_count', False):
                num_frames = self.set_max_frames(effective_start_time, effective_end_time)
            else:
                num_frames = merged_vlm.get('num_frames', 10)

            if os.getenv('LOG_VERBOSE_PROMPTS', 'false').lower() in ('1', 'true', 'yes'):
                logger.debug(f"User Prompt: {user_prompt}\nSystem Prompt: {system_prompt}")

            max_retries = merged_vlm.get('max_retries', 1)
            retry_delay = 0.5

            vlm_response = None
            response_content = None
            verification_successful = False
            vlm_failure_reason = None  # set if VLM parse fails on last attempt

            for attempt in range(max_retries + 1):
                # Start timer outside try so it is always accessible in except clauses.
                # _vlm_observed guards against double-counting on parse errors, which
                # only occur after analyze_video_url() has already returned (and been
                # observed), unlike API exceptions which fire before observe() is called.
                _attempt_start = time.time()
                _vlm_observed = False
                try:
                    logger.info("VLM request sent (attempt %d/%d, base64=%s) [sensor=%s category=%s start=%s end=%s]",
                                attempt + 1, max_retries + 1, self.vlm_media_source_using_base64,
                                sensor_id, message.get('category', 'N/A'), message.get('timestamp', 'N/A'), message.get('end', 'N/A'))
                    start = time.time()
                    vlm_response: ChatCompletionMessage = self._analyze_video_url_with_mode(
                        vlm_video_url,
                        user_prompt,
                        system_prompt,
                        num_frames=num_frames,
                        use_base64=self.vlm_media_source_using_base64,
                        config_overrides=merged_vlm,
                    )
                    duration = round(time.time() - start, 3)
                    latency['vlmRequest'] = {'success': vlm_response is not None, 'duration': duration}
                    observe_vlm_duration(duration, sensor_id)
                    _vlm_observed = True
                    logger.info("VLM response received [sensor=%s category=%s] duration=%.3fs",
                                sensor_id, message.get('category', 'N/A'), duration)

                    # Raw response will be logged once below using response_content
                    response_content = vlm_response.content
                    if os.getenv('LOG_VERBOSE_VLM_RESPONSE', 'false').lower() in ('1', 'true', 'yes'):
                        logger.debug(f"Raw VLM response: {response_content}")

                    verification_successful, response_content = self._apply_vlm_response(
                        message,
                        response_content,
                        merged_vlm,
                        storage_video_url,
                        latency,
                    )
                    break # Terminal outcome (success or pluggable-parser error)

                except (APITimeoutError, APIConnectionError, InternalServerError, UnprocessableEntityError) as e:
                    # API-level error: analyze_video_url() threw before returning,
                    # so VLM_DURATION was never observed for this attempt.
                    if not _vlm_observed:
                        observe_vlm_duration(
                            round(time.time() - _attempt_start, 3),
                            sensor_id,
                        )
                    if attempt < max_retries:
                        logger.warning("VLM API error (attempt %d/%d), retrying: %s", attempt + 1, max_retries + 1, e)
                        self._sleep_retry_with_mode(retry_delay)
                    else:
                        raise e # Let outer handlers handle final failure

                except Exception as e:
                    # Parse/validation error or unexpected error.
                    # If analyze_video_url() threw (not a parse error), _vlm_observed is
                    # still False and we need to observe. If it was a parse error,
                    # _vlm_observed is True and we skip to avoid double-counting.
                    if not _vlm_observed:
                        observe_vlm_duration(
                            round(time.time() - _attempt_start, 3),
                            sensor_id,
                        )
                    if attempt < max_retries:
                        logger.warning("VLM validation/processing error (attempt %d/%d), retrying: %s", attempt + 1, max_retries + 1, e)
                        self._sleep_retry_with_mode(retry_delay)
                    else:
                        vlm_failure_reason = self._apply_vlm_parse_failure(
                            message, e, response_content, storage_video_url, latency
                        )
                        response_content = None
                        break

            publish_future = self._publish_outcome_and_complete(
                message,
                user_prompt,
                system_prompt,
                response_content,
                vlm_failure_reason,
                worker_start_time,
                latency,
            )

            # Process enrichment after publish (async pattern - zero latency impact on alert availability)
            if verification_successful:
                enrichment_result = self._process_enrichment_with_mode(
                    message=message,
                    video_url=vlm_video_url,
                    system_prompt=system_prompt,
                    sensor_id=sensor_id,
                    config_overrides=merged_vlm,
                )
                if enrichment_result:
                    self.enrichment_processor.merge_into_message(message, enrichment_result)
                    self._update_enrichment_with_mode(
                        message,
                        enrichment_result,
                        publish_future=publish_future,
                    )
        except Exception as e:
            self._handle_vlm_exception(
                e,
                message,
                user_prompt,
                system_prompt,
                storage_video_url,
                worker_start_time,
                latency,
            )
            return

    def _prepare_message_context(
        self,
        message: Dict[str, Any],
        sensor_id: Any,
        latency: Dict[str, Any],
        worker_start_time: float,
    ) -> Optional[tuple]:
        """
        Run the confirmed-verdict skip check and resolve prompts.

        Returns ``(user_prompt, system_prompt)``, or ``None`` when the message
        was fully handled (skipped or recorded as a pre-processing failure).
        """
        # Reject messages missing fields the downstream VST stage dereferences
        # directly (``message['timestamp']`` / ``message['end']``). The HTTP JSON
        # endpoint validates these, but producers that bypass it — the protobuf
        # endpoint, a raw Kafka producer, or replay tooling — can still enqueue a
        # malformed message. Without this guard those raise ``KeyError`` deep in
        # ``_resolve_video_url``; record it as a first-class failure instead.
        missing_fields = [
            field for field in ("sensorId", "timestamp", "end") if not message.get(field)
        ]
        if missing_fields:
            logger.error(
                "Dropping malformed message missing required field(s) %s [sensor=%s]",
                missing_fields, message.get('sensorId', 'N/A'),
            )
            record_event_complete(
                worker_start_time,
                message,
                latency,
                failure_reason="malformed_message",
            )
            return None

        # C25: wrap the skip check so state-backend failures surface as a
        # ``VERIFICATION_FAILURES`` event instead of bubbling to
        # ``process_batch_vlm``'s generic ``except Exception`` (which logs
        # but never touches Prometheus).
        try:
            if self._set_message_id_and_should_skip(message, sensor_id):
                return None
        except Exception as exc:
            logger.error(
                "Pre-processing error in confirmed-verdict skip check "
                "[sensor=%s]: %s",
                sensor_id, exc, exc_info=True,
            )
            record_event_complete(
                worker_start_time,
                message,
                latency,
                failure_reason=self._classify_pre_processing_failure(exc),
            )
            return None

        user_prompt, system_prompt = self.prompt_manager.get_prompts_for_message(message)

        if user_prompt is None and system_prompt is None:
            logger.warning("No prompt found [sensor=%s category=%s start=%s end=%s]",
                           sensor_id, message.get('category', 'N/A'), message.get('timestamp', 'N/A'), message.get('end', 'N/A'))
            # C10: record the early-exit so operators watching dashboards
            # can correlate "events stopped flowing" with "alert type has
            # no prompt configured".
            record_event_complete(
                worker_start_time,
                message,
                latency,
                failure_reason="no_prompt",
            )
            return None

        return user_prompt, system_prompt

    def _resolve_video_url(
        self,
        message: Dict[str, Any],
        sensor_id: Any,
        latency: Dict[str, Any],
    ) -> tuple:
        """
        Fetch the VST video URL (with optional overlay-less retry).

        Returns ``(video_url, effective_start_time, effective_end_time,
        vst_error)``; failures are captured in ``vst_error`` with
        ``video_url=None`` rather than raised.
        """
        objects_ids = message.get('objectIds', [])

        # Look up per-alert-type segment anchor override
        alert_type_anchor = None
        alert_type = message.get('category', '')
        if alert_type and self.prompt_manager and self.prompt_manager.alert_config_loader:
            alert_config = self.prompt_manager.alert_config_loader.get_config_for_alert_type(alert_type)
            if alert_config and alert_config.segment_anchor:
                alert_type_anchor = alert_config.segment_anchor
                logger.debug(f"Using per-alert-type segment_anchor='{alert_type_anchor}' for category='{alert_type}'")

        vst_error_captured = None
        # Accumulate wall-clock across every VST attempt for this event
        # (primary + optional retry_without_overlay) and observe once per
        # event after the retry block, so retry-success paths do not inflate
        # ``alert_bridge_vst_duration_seconds_count``. The per-attempt
        # durations are still preserved individually in
        # ``latency['getVideoStreamUrlWithOverlay'|'...WithoutOverlay']``.
        vst_total_duration = 0.0
        video_url = None
        effective_start_time = None
        effective_end_time = None

        try:
            start = time.time()
            video_url, effective_start_time, effective_end_time = self._get_video_stream_url_with_mode(
                sensor_id,
                message['timestamp'],
                message['end'],
                objects_ids=objects_ids,
                latency=latency,
                alert_type_anchor=alert_type_anchor,
            )
            duration = round(time.time() - start, 3)
            latency['getVideoStreamUrlWithOverlay'] = {'success': video_url is not None, 'duration': duration}
            vst_total_duration += duration
            observe_video_length(
                iso_delta_seconds(effective_start_time, effective_end_time),
                sensor_id,
            )
        except VSTError as e:
            duration = round(time.time() - start, 3)
            latency['getVideoStreamUrlWithOverlay'] = {'success': False, 'duration': duration}
            vst_total_duration += duration
            logger.error(
                "VST error getting video URL [sensor=%s category=%s start=%s end=%s]: "
                "type=%s status=%s category=%s body=%s",
                sensor_id, message.get('category', 'N/A'),
                message.get('timestamp', 'N/A'), message.get('end', 'N/A'),
                type(e).__name__, e.status_code, e.category, e.response_body,
            )
            vst_error_captured = e
            video_url = None
            if self.config.get('vst_config', {}).get('retry_without_overlay', False):
                try:
                    logger.info("Retrying video URL without overlay [sensor=%s category=%s start=%s end=%s]",
                                 sensor_id, message.get('category', 'N/A'), message.get('timestamp', 'N/A'), message.get('end', 'N/A'))
                    start = time.time()
                    video_url, effective_start_time, effective_end_time = self._get_video_stream_url_with_mode(
                        sensor_id,
                        message['timestamp'],
                        message['end'],
                        objects_ids=objects_ids,
                        remove_overlay=True,
                        alert_type_anchor=alert_type_anchor,
                    )
                    duration = round(time.time() - start, 3)
                    latency['getVideoStreamUrlWithoutOverlay'] = {'success': video_url is not None, 'duration': duration}
                    vst_total_duration += duration
                    observe_video_length(
                        iso_delta_seconds(effective_start_time, effective_end_time),
                        sensor_id,
                    )
                    vst_error_captured = None
                except VSTError as retry_e:
                    duration = round(time.time() - start, 3)
                    latency['getVideoStreamUrlWithoutOverlay'] = {'success': False, 'duration': duration}
                    vst_total_duration += duration
                    logger.error(
                        "VST error on retry without overlay [sensor=%s category=%s start=%s end=%s]: "
                        "type=%s status=%s category=%s body=%s",
                        sensor_id, message.get('category', 'N/A'),
                        message.get('timestamp', 'N/A'), message.get('end', 'N/A'),
                        type(retry_e).__name__, retry_e.status_code, retry_e.category, retry_e.response_body,
                    )
                    vst_error_captured = retry_e
                    video_url = None
                except Exception as retry_e:
                    duration = round(time.time() - start, 3)
                    latency['getVideoStreamUrlWithoutOverlay'] = {'success': False, 'duration': duration}
                    vst_total_duration += duration
                    logger.error("Unexpected error on retry without overlay [sensor=%s category=%s start=%s end=%s]: %s",
                                 sensor_id, message.get('category', 'N/A'), message.get('timestamp', 'N/A'), message.get('end', 'N/A'), retry_e)
                    video_url = None
        except Exception as e:
            duration = round(time.time() - start, 3)
            latency['getVideoStreamUrlWithOverlay'] = {'success': False, 'duration': duration}
            vst_total_duration += duration
            logger.error("Unexpected error getting video URL [sensor=%s category=%s start=%s end=%s]: %s",
                         sensor_id, message.get('category', 'N/A'), message.get('timestamp', 'N/A'), message.get('end', 'N/A'), e)
            video_url = None

        # Emit VST_DURATION exactly once per event regardless of attempt count.
        observe_vst_duration(round(vst_total_duration, 3), sensor_id)
        return video_url, effective_start_time, effective_end_time, vst_error_captured

    def _transform_video_urls(self, video_url: str) -> tuple:
        """Return ``(vlm_video_url, storage_video_url)`` for the consumers.

        VLM needs the external URL only in remote mode; ES/UI always needs
        the external URL.
        """
        if self.url_transform_enabled:
            return (
                transform_video_url(video_url, to_external=not is_vlm_local()),
                transform_video_url(video_url, to_external=True),
            )
        return video_url, video_url

    def _handle_media_collection_failure(
        self,
        message: Dict[str, Any],
        vst_error_captured,
        worker_start_time: float,
        latency: Dict[str, Any],
    ) -> None:
        sensor_id = message.get('sensorId')
        vst_code, vst_status = self._classify_vst_failure(vst_error_captured)
        logger.warning("Media collection failed [sensor=%s category=%s start=%s end=%s] reason=%s",
                       sensor_id, message.get('category', 'N/A'), message.get('timestamp', 'N/A'), message.get('end', 'N/A'), vst_status)
        user_prompt, system_prompt = self.prompt_manager.get_prompts_for_message(message)
        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source=None,
                verification_response_code=vst_code,
                verification_response_status=vst_status,
                verdict="verification-failed",
                error_source=ERROR_SOURCE_MEDIA_DOWNLOAD,
            ),
            latency=latency,
            include_latency=self.include_latency_info,
        )
        publish_future = self._publish_error_with_mode(
            message,
            user_prompt,
            system_prompt,
            {},
        )
        self._complete_event_after_publish(
            publish_future,
            worker_start_time,
            message,
            latency,
            failure_reason=self._classify_vst_failure_reason(vst_error_captured),
        )

    def _handle_url_validation_failure(
        self,
        message: Dict[str, Any],
        storage_video_url: Optional[str],
        worker_start_time: float,
        latency: Dict[str, Any],
    ) -> None:
        sensor_id = message.get('sensorId')
        logger.error("URL validation failed [sensor=%s category=%s start=%s end=%s]",
                     sensor_id, message.get('category', 'N/A'), message.get('timestamp', 'N/A'), message.get('end', 'N/A'))
        user_prompt, system_prompt = self.prompt_manager.get_prompts_for_message(message)
        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source=storage_video_url,
                verification_response_code=400,
                verification_response_status="Video URL could not be validated or was unreachable",
                verdict="verification-failed",
                error_source=ERROR_SOURCE_MEDIA_DOWNLOAD,
            ),
            latency=latency,
            include_latency=self.include_latency_info,
        )
        publish_future = self._publish_error_with_mode(
            message,
            user_prompt,
            system_prompt,
            {},
        )
        self._complete_event_after_publish(
            publish_future,
            worker_start_time,
            message,
            latency,
            failure_reason="url_validation",
        )

    def _apply_vlm_response(
        self,
        message: Dict[str, Any],
        response_content: Optional[str],
        merged_vlm: Dict[str, Any],
        storage_video_url: Optional[str],
        latency: Dict[str, Any],
    ) -> tuple:
        """
        Parse the VLM response and merge the outcome into the message.

        Returns ``(verification_successful, response_content)``. Pluggable
        parser failures are terminal (deterministic parser bug — no point
        retrying); validation errors on the built-in path raise so the caller
        can retry.
        """
        if self._pluggable_parser is not None:
            try:
                parsed = self._pluggable_parser.parse(response_content)
                if not isinstance(parsed, dict):
                    raise TypeError(
                        f"Pluggable parser returned {type(parsed).__name__}, expected dict"
                    )
            except Exception as parser_error:
                _apply_pluggable_parser_error(
                    message, parser_error,
                    video_source=storage_video_url,
                    latency=latency,
                    include_latency=self.include_latency_info,
                )
                # Route to _publish_error_with_mode via the post-loop
                # dispatcher (which branches on ``response_content is not
                # None``) so parser crashes are not mislabeled as
                # successful publishes.
                return False, None
            _apply_pluggable_parser_output(
                message, parsed,
                video_source=storage_video_url,
                latency=latency,
                include_latency=self.include_latency_info,
            )
            return True, response_content

        # Use the already-merged per-category VLM config so ``model`` /
        # ``response_format`` / ``json_parser`` overrides applied to the
        # *request* also apply to the *response parser*.
        vlm_data = VLMResponse.model_validate_text(
            response_content,
            model_name=merged_vlm.get('model', ''),
            response_format=merged_vlm.get('response_format', 'auto'),
            json_config=merged_vlm.get('json_parser'),
        )
        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=vlm_data,
                video_source=storage_video_url,
                verification_response_code=200,
                verification_response_status="OK",
            ),
            latency=latency,
            include_latency=self.include_latency_info,
        )
        return True, response_content

    def _apply_vlm_parse_failure(
        self,
        message: Dict[str, Any],
        error: Exception,
        response_content: Optional[str],
        storage_video_url: Optional[str],
        latency: Dict[str, Any],
    ) -> str:
        """Merge the terminal parse-failure response; returns the failure reason."""
        raw_excerpt = response_content if response_content else "<no response>"
        logger.warning(
            "VLM response parsing failed "
            "[sensor=%s category=%s model=%s endpoint=%s]: %s | "
            "Raw VLM response: %s",
            message.get('sensorId'),
            message.get('category', 'N/A'),
            self.vlm_client.model,
            self.vlm_client.base_url,
            error,
            raw_excerpt,
        )

        if not response_content or not response_content.strip():
            parse_status = (
                "VLM returned an empty response, the model produced no output "
                "for this video. This may indicate the model failed to process "
                "the input or encountered an internal issue."
            )
        elif "not in expected format" in str(error):
            parse_status = (
                "VLM response not in expected YES/NO format, the model returned "
                f"free-form text instead of a structured verdict. Raw response: '{raw_excerpt}'"
            )
        else:
            parse_status = (
                f"VLM response failed validation, {error}. "
                f"Raw response: '{raw_excerpt}'"
            )

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source=storage_video_url,
                verification_response_code=500,
                verification_response_status=parse_status,
                verdict="verification-failed",
                error_source=ERROR_SOURCE_VLM_SCHEMA,
            ),
            latency=latency,
            include_latency=self.include_latency_info,
        )
        return "vlm_parse_failure"

    def _publish_outcome_and_complete(
        self,
        message: Dict[str, Any],
        user_prompt: str,
        system_prompt: Optional[str],
        response_content: Optional[str],
        vlm_failure_reason: Optional[str],
        worker_start_time: float,
        latency: Dict[str, Any],
    ) -> Optional[Future]:
        # C23: ``elasticReadyAt`` is stamped by ``record_event_complete``;
        # when the async elastic sink is enabled it fires from the publish
        # future's done-callback so the stamp reflects the real sink-write
        # completion wall-clock, not the submit-queue-enqueue time.
        if response_content is not None:
            publish_future = self._publish_success_with_mode(
                message,
                user_prompt,
                system_prompt,
                response_content,
            )
        else:
            publish_future = self._publish_error_with_mode(
                message,
                user_prompt,
                system_prompt,
                {},
            )

        self._complete_event_after_publish(
            publish_future,
            worker_start_time,
            message,
            latency,
            failure_reason=vlm_failure_reason,
        )
        return publish_future

    def _apply_vlm_exception(
        self,
        exc: Exception,
        message: Dict[str, Any],
        storage_video_url: Optional[str],
        latency: Dict[str, Any],
    ) -> tuple:
        """Classify a failed VLM call and merge the error response into the
        message. Returns ``(failure_reason, log_label)``. Shared by the sync
        and event_loop error paths."""
        code, status_prefix, failure_reason, log_label = (
            500, "Video verification could not be completed", "unknown", "VLM analysis failed",
        )
        for exc_type, mapped in _VLM_API_ERROR_CLASSIFICATION:
            if isinstance(exc, exc_type):
                code, status_prefix, failure_reason, log_label = mapped
                break
        root_cause = self._extract_root_cause(exc)
        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source=storage_video_url,
                verification_response_code=code,
                verification_response_status=f"{status_prefix}, {root_cause}",
                verdict="verification-failed",
                error_source=ERROR_SOURCE_VLM_API,
            ),
            latency=latency,
            include_latency=self.include_latency_info,
        )
        return failure_reason, log_label

    def _log_vlm_exception(
        self,
        log_label: str,
        message: Dict[str, Any],
        exc: Exception,
    ) -> None:
        logger.error("%s [sensor=%s category=%s model=%s endpoint=%s start=%s end=%s]: %s",
                     log_label, message.get('sensorId'), message.get('category', 'N/A'),
                     self.vlm_client.model, self.vlm_client.base_url,
                     message.get('timestamp', 'N/A'), message.get('end', 'N/A'), exc)

    def _handle_vlm_exception(
        self,
        exc: Exception,
        message: Dict[str, Any],
        user_prompt: str,
        system_prompt: Optional[str],
        storage_video_url: Optional[str],
        worker_start_time: float,
        latency: Dict[str, Any],
    ) -> None:
        """Publish the error document and metrics for a failed VLM call."""
        failure_reason, log_label = self._apply_vlm_exception(
            exc, message, storage_video_url, latency
        )
        publish_future = self._publish_error_with_mode(
            message,
            user_prompt,
            system_prompt,
            {},
        )
        self._complete_event_after_publish(
            publish_future,
            worker_start_time,
            message,
            latency,
            failure_reason=failure_reason,
        )
        self._log_vlm_exception(log_label, message, exc)

    @staticmethod
    def _extract_root_cause(exc: Exception, max_len: int = 150) -> str:
        """One-line concise root cause for verificationResponseStatus."""
        cause = exc.__cause__ or exc
        name = type(cause).__name__
        msg = str(cause)[:max_len]
        return f"{name}: {msg}" if msg else name

    def _set_message_id_and_should_skip(self, message: Dict[str, Any], sensor_id: Any) -> bool:
        """
        Compute and attach a stable fingerprint (as `message["Id"]`) and return True if the
        message should be skipped because a confirmed verdict already exists for that fingerprint.
        """
        fingerprint = self._compute_fingerprint(message)
        if not fingerprint:
            return False

        # Set early for downstream use (logs, sinks, redis keys, etc.)
        message["Id"] = fingerprint

        if self.redis_handler is None:
            return False

        try:
            verdict_confirmed = self._run_redis_operation_with_mode(
                "is_verdict_confirmed",
                self.redis_handler.is_verdict_confirmed,
                fingerprint,
            )
        except Exception as exc:
            logger.warning(
                "Failed to check confirmed verdict; continuing processing",
                extra={
                    "fingerprint": fingerprint,
                    "sensorId": sensor_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            verdict_confirmed = False

        if verdict_confirmed:
            logger.info(
                "Skipping processing: confirmed verdict exists",
                extra={"fingerprint": fingerprint, "sensorId": sensor_id},
            )
            # C9: make the short-circuit visible on dashboards. This is
            # the counter that keeps the C2 reconciliation invariant
            # valid — without it, events silently disappear between
            # ``EVENTS_AFTER_DEDUP`` and ``EVENTS_TOTAL`` during any
            # incident that re-delivers already-confirmed events
            # (e.g. Kafka consumer-group rebalance). C21: pass the
            # message so the per-sensor variant increments too.
            inc_events_skipped_confirmed(message)
            return True

        return False

    @staticmethod
    def _compute_fingerprint(message: Dict[str, Any]) -> Optional[str]:
        """Return the correct fingerprint type for the message, or None if unavailable."""
        if is_alert(message):
            return generate_alert_fingerprint(message)
        return generate_incident_fingerprint(message)

    @staticmethod
    def _classify_vst_failure(vst_error) -> tuple:
        """Map a captured VSTError (or None) to (response_code, response_status).

        Returns appropriate HTTP-style codes and user-facing messages based on
        the specific VST failure type, instead of always returning 404.
        """
        if vst_error is None:
            return 404, "No video recording found for the requested time"
        if isinstance(vst_error, VSTRecordingNotFoundError):
            return 404, "No video recording found for the requested time"
        if isinstance(vst_error, VSTOverloadedError):
            return vst_error.status_code or 503, "VST service overloaded"
        if isinstance(vst_error, VSTTimeoutError):
            return 504, "VST request timed out"
        if isinstance(vst_error, VSTUnavailableError):
            return vst_error.status_code or 503, "VST service unavailable"
        if isinstance(vst_error, VSTClientError):
            return vst_error.status_code or 400, f"VST request error (HTTP {vst_error.status_code})"
        if isinstance(vst_error, VSTError):
            if vst_error.category == "missing_video_url":
                return 502, "VST returned response without video URL"
            return vst_error.status_code or 500, "VST error: could not retrieve video"
        return 500, "VST error: could not retrieve video"

    def _complete_event_after_publish(
        self,
        publish_future,
        worker_start_time,
        message,
        latency,
        failure_reason=None,
    ):
        """Fire ``record_event_complete`` once the sink publish finishes (C23).

        In sync sink mode (``async_io_enabled=False``), the publish
        already completed inline and ``publish_future`` is ``None`` —
        we fire the recorder immediately so ``elasticReadyAt`` reflects
        the true wall-clock when the sink write returned (same as the
        pre-C23 behavior on the sync path).

        In async sink mode, ``publish_future`` is the sink-submission
        ``Future`` returned by ``_submit_sink_operation_with_mode``. We
        defer the recorder call to the future's done-callback so
        ``elasticReadyAt`` is stamped when the async sink write
        *actually finishes* — closing the C23 undercount where
        ``E2E_DURATION`` previously excluded the async-sink queue and
        ES-write time, and silently shortened by a variable amount the
        moment the ``async_io_enabled`` flag flipped.

        The closure captures ``message`` and ``latency`` by reference.
        The async sink thread reads from a deep-copy of ``message``
        (made inside ``_submit_sink_operation_with_mode`` so sink
        payloads are immutable from the caller's perspective), so
        stamping the live dict from the done-callback cannot race
        with the sink write that fired this callback in the first
        place. The per-event Prometheus counters are already
        thread-safe via the ``prometheus_client`` internal lock.
        """
        def _finalize(_future=None):
            record_event_complete(
                worker_start_time,
                message,
                latency,
                failure_reason=failure_reason,
            )

        if publish_future is None:
            # Sync mode (``_submit_sink_operation_with_mode`` returned
            # ``None``): the publish has already completed, so fire the
            # recorder inline. Same observable behavior as pre-C23.
            _finalize()
        else:
            # Async mode: defer until the sink future resolves. If the
            # future is already done at this point (rare but possible
            # when the executor raced ahead), ``add_done_callback``
            # runs the callback immediately.
            publish_future.add_done_callback(_finalize)

    @staticmethod
    def _classify_pre_processing_failure(exc) -> str:
        """Map an exception raised before the VST/VLM pipeline starts to
        a ``VERIFICATION_FAILURES`` reason label (C25).

        The only pre-processing work that actually runs Redis queries
        is the confirmed-verdict skip check in
        ``_set_message_id_and_should_skip``. Any Redis-client exception
        (``redis.ConnectionError``, ``redis.TimeoutError``, the wrapping
        exceptions thrown by the async runtime on its sync-fallback
        re-raise, etc.) routes to ``redis_unavailable`` so operators
        triaging a Redis outage see it as its own dashboard row rather
        than a generic ``unknown``.

        Non-Redis exceptions (defensive fallback — a hypothetical bug
        in ``_compute_fingerprint``, say) fold into ``unknown`` so the
        event is still counted but not misattributed.

        We duck-type on the MRO class names rather than importing the
        Redis package here — keeps the classifier decoupled from
        whichever Redis driver the deployment happens to use (redis-py,
        aioredis, fakeredis-in-tests, a wrapped variant, etc.).
        """
        for cls in type(exc).__mro__:
            if "redis" in cls.__name__.lower():
                return "redis_unavailable"
        return "unknown"

    @staticmethod
    def _classify_vst_failure_reason(vst_error) -> str:
        """Map a captured VSTError (or None) to a Prometheus ``reason`` label.

        Symmetry with the VLM side: ``_process_single_message`` emits
        ``vlm_timeout`` / ``vlm_connection_error`` / ``vlm_server_error`` /
        ``vlm_invalid_payload`` from its own exception handlers, and
        operators triaging a dashboard alert need the same granularity
        for VST failures. Folding every VST failure into a single
        ``vst_failure`` bucket would mean an operator seeing a VST alert
        would have to open logs just to know whether to page the VST
        team (timeout / unavailable / overloaded) or Alert-Agent itself
        (missing_video_url / client 4xx).

        Migration note for dashboards: any existing panel filtering on
        ``VERIFICATION_FAILURES{reason="vst_failure"}`` must switch to
        ``reason=~"vst_.*"`` after this change. ``vst_failure`` is no
        longer emitted.
        """
        if vst_error is None:
            # No captured exception but the URL was None — the VST call
            # succeeded HTTP-wise but returned no usable video URL for
            # the requested time range. Classify as ``vst_not_found``
            # (same bucket as ``VSTRecordingNotFoundError``) so the
            # dashboard row is semantically consistent.
            return "vst_not_found"
        if isinstance(vst_error, VSTTimeoutError):
            return "vst_timeout"
        if isinstance(vst_error, VSTOverloadedError):
            return "vst_overloaded"
        if isinstance(vst_error, VSTRecordingNotFoundError):
            return "vst_not_found"
        if isinstance(vst_error, VSTUnavailableError):
            return "vst_unavailable"
        if isinstance(vst_error, VSTClientError):
            return "vst_client_error"
        if isinstance(vst_error, VSTError):
            # Bare VSTError covers anything not captured by the specific
            # subclasses above, including the ``missing_video_url`` case
            # flagged by ``_classify_vst_failure`` as a 502.
            return "vst_server_error"
        # Non-VSTError exception that slipped through (e.g. a generic
        # RuntimeError from the retry-without-overlay path's broad
        # ``except Exception``). Folds into a catch-all so operators
        # can still see the event class the same way
        # ``EVENTS_DROPPED{reason="unknown"}`` works for filter drops.
        return "vst_unknown"

    def _create_validation_error_responses(self, original_messages, validated_entities):
        """
        Create error responses for failed validation entities.

        Args:
            failed_entities: List of original entities that failed validation
            validated_entities: List of successfully validated entities

        Returns:
            List of AlertResponseEntity error responses
        """
        from schemas.response_entity.models import AlertResponseEntity
        from datetime import datetime, timezone
        import json

        # Get IDs of successfully validated entities
        validated_ids = {entity.id for entity in validated_entities}

        error_responses = []

        for message in original_messages:
            # Handle both JSON strings and dict objects
            if isinstance(message, str):
                try:
                    message_dict = json.loads(message)
                except json.JSONDecodeError:
                    message_dict = {"id": "invalid_json", "sensorId": "unknown"}
            else:
                message_dict = message

            message_id = message_dict.get('id', f'unknown_{hash(str(message)) % 10000}')

            # If this message wasn't successfully validated, create error response
            if message_id not in validated_ids:
                # Create error response for validation failure
                from schemas.response_entity.models.responses import (
                    AlertResponseEntity, AlertInfo, EventInfo, VerificationInfo
                )
                from schemas.shared.enums import AlertSeverity, AlertStatus

                error_response = AlertResponseEntity(
                    id=message_id,
                    version="1.0",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    sensor_id=message_dict.get('sensorId', 'unknown'),
                    stream_name=None,
                    video_path=message_dict.get('videoPath', ''),
                    cv_metadata_path=None,
                    confidence=None,
                    alert=AlertInfo(
                        severity=AlertSeverity.HIGH,
                        status=AlertStatus.ACTIVE,
                        type="validation_error",
                        description="Message validation failed - check required fields and format"
                    ),
                    event=EventInfo(
                        type="validation_failure",
                        description="Request message failed validation"
                    ),
                    verification=VerificationInfo(
                        status="FAILURE",
                        error_string="Input validation failed",
                        result=False,
                        confidence=0.0,
                        verification_method="VALIDATION",
                        verified_by="AnomalyEnhancer",
                        verified_at=datetime.now(timezone.utc).isoformat(),
                        notes="Message failed input validation",
                        debug=None,
                        description="Could not validate input message",
                        alert_reasoning="Request validation failed"
                    ),
                    meta_labels=[]
                )
                error_responses.append(error_response)

        return error_responses

    def _send_error_responses(self, error_responses, worker_id):
        """
        Send error responses to Redis streams.

        Args:
            error_responses: List of AlertResponseEntity error responses
            worker_id: Worker ID for logging
        """
        try:
            from mdx.stream_message import StreamMessage
            from datetime import datetime, timezone

            # Convert error responses to StreamMessage format
            stream_messages = []
            for error_response in error_responses:
                # Convert to dict and then to StreamMessage
                response_data = error_response.model_dump(by_alias=True)

                stream_message = StreamMessage(
                    id=error_response.id,
                    timestamp=datetime.now(timezone.utc),
                    data=response_data,
                    metadata={
                        'source': 'alert_agent_validation',
                        'worker_id': worker_id,
                        'error_type': 'validation_error'
                    },
                    raw_data=None,
                    core_fields=None
                )
                stream_messages.append(stream_message)

            # Send to Redis using the sink
            if stream_messages:
                self.sink.write(stream_messages)
                logger.info(f"Sent {len(stream_messages)} validation error responses to Redis", extra={
                    "worker_id": worker_id,
                    "error_responses_count": len(stream_messages)
                })

        except Exception as e:
            logger.error(f"Failed to send error responses to Redis: {e}", extra={
                "worker_id": worker_id,
                "error_responses_count": len(error_responses)
            })


def start_fastapi(parent_pid: Optional[int] = None, shared_fleet_state=None):
    """Start FastAPI server for Alert Bridge HTTP endpoints."""
    if shared_fleet_state is not None:
        # Adopted before uvicorn imports the app, so the first probe already
        # reads the same state the parent publishes -- and reads it whether or
        # not metrics are exported.
        fleet_state.attach(shared_fleet_state)
    if parent_pid is not None:
        # Same contract as the pipeline workers: an orphaned API child would
        # keep answering on the health port for an instance that is gone.
        # SIGTERM, because uvicorn turns it into a bounded connection drain --
        # unlike a pipeline child, it has no unbounded work to finish.
        _exit_when_parent_dies(parent_pid, signal.SIGTERM)
    try:
        port = int(os.getenv("FASTAPI_PORT", 9080))
        logger.info(f"Starting Alert Bridge FastAPI server on port {port}...")
        uvicorn.run("web.main:app", host="0.0.0.0", port=port)
    except Exception as e:
        logger.error(f"FastAPI server failed to start: {e}")
        raise


def _start_prometheus_metrics_server(port: int) -> None:
    """Start the scrape server, aggregating child-process metrics if enabled."""
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        prometheus_multiprocess.MultiProcessCollector(registry)
        start_prometheus_server(port, registry=registry)
        return

    start_prometheus_server(port)


def _on_pipeline_process_exit(process: Optional[Any], expected: bool) -> None:
    """Record the exit, then drop the dead child's metric shards."""
    from metrics.recorder import inc_pipeline_process_exit
    inc_pipeline_process_exit("shutdown" if expected else "unexpected")
    _mark_prometheus_process_dead(process)


def _mark_prometheus_process_dead(process: Optional[Any]) -> None:
    """Tell prometheus_client to drop live gauge shards for a stopped child."""
    if not PROMETHEUS_ENABLED or process is None:
        return
    if not os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        return
    try:
        prometheus_multiprocess.mark_process_dead(process.pid)
    except Exception:
        logger.debug("Failed to mark Prometheus child process dead", exc_info=True)


def _exit_when_parent_dies(parent_pid: int, sig: int = signal.SIGKILL) -> None:
    """Ask the kernel to signal this child when the supervisor disappears.

    An orphaned child keeps its consumer-group membership, which stalls the
    partitions it owns and blocks the next run's offset reset. Linux-only;
    elsewhere teardown relies solely on the supervisor.

    SIGKILL by default: there is no supervisor left to escalate, and a SIGTERM
    to a pipeline child lands in a graceful shutdown that drains in-flight work
    for as long as it takes -- so the orphan holds the very slot this releases.
    The API child passes SIGTERM instead, where it means a bounded connection
    drain rather than an unbounded one.
    """
    try:
        import ctypes

        PR_SET_PDEATHSIG = 1
        if ctypes.CDLL(None, use_errno=True).prctl(PR_SET_PDEATHSIG, sig) != 0:
            # Blocked by seccomp rather than absent. Worth a line: without it
            # an orphaned child keeps its consumer-group slot until killed.
            logger.debug("PR_SET_PDEATHSIG rejected (errno=%s)", ctypes.get_errno())
    except Exception:
        logger.debug("PR_SET_PDEATHSIG unavailable on this platform", exc_info=True)

    # Unconditional, and last: the parent may already have exited, in which
    # case there is no signal left to arrange for. Behind the two failure
    # returns this used to be skipped exactly when it was the only check left.
    if os.getppid() != parent_pid:
        os._exit(0)


def _log_instance_concurrency(enhancer: "AnomalyEnhancer", process_count: int) -> None:
    """Log what the *backend* sees, which is per-process caps times processes.

    Every other concurrency log line is written from inside a child and shows
    per-process values, so an operator reading ``max_vlm_concurrent=2`` four
    times has no way to see that the VLM endpoint is being offered 8. Emitted
    from one child rather than the parent so the numbers are the resolved
    ones, defaults included, instead of a second copy of that resolution.
    """
    logger.warning(
        "Effective instance concurrency across %d processes: vlm=%d (%d x %d), "
        "vst=%d (%d x %d), dispatch_in_flight=%d (%d x %d), worker_threads=%d (%d x %d). "
        "Downstream services see these totals, not the per-process caps below. "
        "max_vlm_concurrent must be sized against peak_survivor_rate / processes.",
        process_count,
        process_count * enhancer.max_vlm_concurrent, process_count, enhancer.max_vlm_concurrent,
        process_count * enhancer.max_vst_concurrent, process_count, enhancer.max_vst_concurrent,
        process_count * enhancer.async_dispatch_max_in_flight,
        process_count, enhancer.async_dispatch_max_in_flight,
        process_count * enhancer.num_workers, process_count, enhancer.num_workers,
    )


def _publish_readiness(enhancer: "AnomalyEnhancer", index: int,
                       ready_event: Optional[Any], started: bool = True) -> bool:
    """Mirror the live assignment into the readiness signal and the gauge.

    Readiness is not a milestone that a process passes once. A rebalance can
    take every partition away from a member that is still running, and until
    it is given some back it is serving nothing -- the health endpoint and the
    fleet gauge both have to say so.
    """
    # The gauge and the drain budget are the same in every deployment shape,
    # so they are handled by the enhancer; only the supervisor signalling
    # below is particular to running under one.
    enhancer._publish_assignment_state()
    # Decided, not non-empty. With more group members than a topic has
    # partitions a member legitimately owns none of it -- the documented
    # replicas x processes > partitions rollout -- and requiring work here
    # would leave it unready for good. The count is reported separately.
    ready = enhancer.source.is_ready()
    if ready_event is not None:
        if not ready:
            # Losing partitions is reported immediately, whatever else this
            # process is doing.
            ready_event.clear()
        elif started:
            # Raised only once this process has finished starting. The first
            # assignment arrives from inside the join, while the dispatcher is
            # still being built, and announcing then tells a producer to start
            # sending to a pipeline that cannot yet take it.
            ready_event.set()
    return ready


def _run_pipeline_process(config_path: str, index: int, parent_pid: int, process_count: int = 1,
                          ready_event: Optional[Any] = None,
                          join_deadline: Optional[float] = None) -> None:
    """Child entry point: one independent consume + dispatch stack."""
    _exit_when_parent_dies(parent_pid)

    def shutdown_handler(signum, frame):
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGQUIT):
        signal.signal(sig, shutdown_handler)

    logger.info("Pipeline process %d starting (pid=%d)", index, os.getpid())
    try:
        # Child 0 owns the work that belongs to the instance rather than to a
        # pipeline; the others never start it. Any child exiting takes the
        # instance down, so there is no state where that work has no owner.
        enhancer = AnomalyEnhancer(config_path, instance_leader=(index == 0),
                                   seed_shared_store=False)
        # The supervisor publishes the fleet counts for every slot; a child
        # writing its own would overwrite the fleet's totals with 1/1.
        enhancer._publishes_own_fleet_state = False
        # Construction is where children contend on Elasticsearch, and it can
        # take tens of seconds with several of them; the group join then adds
        # its own. Both have to finish before this child counts as ready, or
        # the instance announces readiness while some partitions are still
        # unowned and a producer writes past them.
        # Registered before the wait so the assignment gauge is live from the
        # first callback. It cannot raise readiness yet: that waits until this
        # process has finished starting, below.
        startup = {"complete": False}
        enhancer.source.set_assignment_change_hook(
            lambda: _publish_readiness(enhancer, index, ready_event,
                                       started=startup["complete"])
        )
        # A shared wall-clock deadline, not a shared duration. Given the same
        # number, the child started counting after its spawn and its
        # constructor, so its window always ended after the parent's and the
        # parent killed children a second from joining. time.time() rather
        # than monotonic: the clocks are in different processes.
        if join_deadline is None:
            join_budget = MIN_FLEET_WAIT_SECONDS
        else:
            # The instance's final readiness instant, not a duration and not
            # a floor. A floor let a child that constructed slowly wait past
            # the moment the parent and the watchdog had already given up,
            # so the grant was one nobody honoured. Sharing the instant means
            # a child that starts late simply gets less of it -- and if that
            # is not enough, the budget is what has to be raised.
            join_budget = join_deadline - time.time()
            if join_budget <= 0:
                raise RuntimeError(
                    f"pipeline process {index} reached its consumer-group join "
                    f"after the instance's startup deadline; raise "
                    f"alert_agent.startup_timeout_seconds"
                )
        if not enhancer.source.await_ready(timeout=join_budget):
            # Restartable rather than fatal: a broker blip should recover on
            # the next attempt. What must not happen is reporting ready, which
            # would let a producer publish past an offset nobody is reading.
            raise RuntimeError(
                f"pipeline process {index} could not join the consumer group"
            )
        startup["complete"] = True
        # Logged before the signal, not after. Setting the event wakes the
        # supervisor, which announces the instance; doing that first left the
        # two racing to write the same log, and the ordering only held because
        # a futex wake is slower than the next line. Signalling last makes the
        # child's own line unambiguously first.
        logger.info("Pipeline process %d ready (pid=%d)", index, os.getpid())
        _publish_readiness(enhancer, index, ready_event)
        if index == 0:
            _log_instance_concurrency(enhancer, process_count)
        if not enhancer.process_anomalies():
            # The same rule the single-process path follows. Exiting 0 here
            # still failed the instance, because the supervisor treats any
            # child exit as unexpected -- but it logged exitcode=0 for a crash.
            raise RuntimeError(
                f"pipeline process {index}: the consume loop stopped on an error"
            )
    except SystemExit:
        pass
    except Exception:
        logger.error("Pipeline process %d failed", index, exc_info=True)
        raise
    logger.info("Pipeline process %d stopped", index)


def _start_pipeline_process(config_path: str, index: int, process_count: int,
                            ready_event: Optional[Any] = None,
                            join_deadline: Optional[float] = None) -> Any:
    process = _pipeline_mp_context().Process(
        target=_run_pipeline_process,
        args=(config_path, index, os.getpid(), process_count, ready_event,
              join_deadline),
        name=f"ab-pipeline-{index}",
    )
    process.start()
    return process


def readiness_windows(fleet_wait: float):
    """The announce window, and the margin the watchdog needs beyond it.

    The window is what the budget has left, not more: padding it with a
    reserved share pushed the whole of startup past
    ``startup_timeout_seconds``, and that deadline is the contract. A budget
    too small for the work is refused up front instead.

    The margin exists only so the watchdog outlasts the window rather than
    tying with it -- matched exactly, which of the two fired first came down
    to microseconds of thread start-up, and the readiness diagnostic is the
    more useful one. Returned together so the two cannot drift apart, which
    they have twice.
    """
    return fleet_wait, WATCHDOG_MARGIN_SECONDS


def _first_unset(ready_events: List[Any]):
    """The first worker not currently ready, or ``(None, None)`` if all are."""
    for index, event in enumerate(ready_events):
        if not event.is_set():
            return index, event
    return None, None


def _announce_when_all_ready(ready_events: List[Any], on_ready: Callable[[], None],
                             on_timeout: Optional[Callable[[], None]] = None,
                             timeout: float = MIN_FLEET_WAIT_SECONDS) -> None:
    """Call ``on_ready`` once every child has joined the consumer group.

    Bounded so the wait cannot outlive the run silently, and never announced
    on expiry: a partially-joined instance leaves partitions unowned, and
    saying otherwise is what this whole path exists to prevent. ``on_timeout``
    ends the run instead of leaving it there: the partitions the missing child
    would own stay unowned until something reassigns them, and only a restart
    does -- so a container that sits in this state is a silent outage.
    """
    def wait() -> None:
        deadline = time.monotonic() + timeout
        while True:
            index, event = _first_unset(ready_events)
            if event is None:
                # Every worker ready at the same moment, not merely each one at
                # some point. Waited in sequence, an early worker could be
                # revoked while the wait was still blocked on a later one, and
                # the announcement went out with part of the fleet unassigned.
                break
            granted = max(0.0, deadline - time.monotonic())
            if not event.wait(granted):
                # The budget covers the fleet, so a child reached late gets
                # whatever its peers left. Reporting the whole budget here sent
                # operators after a child that had in fact been given seconds.
                logger.error(
                    "Pipeline process %d was not ready within its %.0fs of the "
                    "%.0fs startup budget; stopping the instance so its "
                    "partitions can be reassigned",
                    index, granted, timeout,
                )
                if on_timeout is not None:
                    on_timeout()
                return
        on_ready()

    threading.Thread(target=wait, name="ab-readiness", daemon=True).start()


def run_multi_process_pipeline(config_path: str, process_count: int,
                               on_ready: Optional[Callable[[], None]] = None,
                               watch: Optional[List[Any]] = None,
                               readiness_timeout: float = MIN_FLEET_WAIT_SECONDS,
                               disarm_watchdog: Optional[Callable[[], None]] = None) -> None:
    """Fork ``process_count`` pipeline children and supervise them until shutdown."""
    global _pipeline_supervisor

    # One per slot rather than a Barrier: readiness is announced once, when
    # the last child arrives, and a Barrier would also make every child wait
    # for its peers before it could start consuming.
    def publish_fleet_state() -> None:
        from metrics.recorder import set_pipeline_process_counts
        supervisor = _pipeline_supervisor
        alive = sum(1 for p in (supervisor.processes if supervisor else []) if p.is_alive())
        ready = sum(1 for e in ready_events if e.is_set())
        # Both channels: the array is what health reads, the gauges are what
        # a scrape reads. Neither depends on the other being enabled.
        fleet_state.publish(process_count, alive, ready)
        set_pipeline_process_counts(process_count, alive, ready)

    ready_events = [_pipeline_mp_context().Event() for _ in range(process_count)]
    # The one moment both sides count to. The parent's watchdog starts now,
    # before any child exists, so a child handed the same duration would
    # outlive it by its own spawn and construction time.
    # One instant for all three: the parent's announce thread, the watchdog
    # (extended to match by the caller) and every child. Three clocks that
    # merely started from the same number drifted apart by whatever spawn and
    # construction cost, and each fix that widened one of them left the others
    # behind.
    announce_timeout, _ = readiness_windows(readiness_timeout)
    join_deadline = time.time() + announce_timeout
    readiness_failed = threading.Event()

    def on_readiness_timeout(disarm=None) -> None:
        # Disarmed first: the teardown this starts runs the shutdown timeline,
        # and a watchdog firing partway through it exits the process from the
        # signal handler, abandoning terminate/kill/reap. The children would
        # then die only by PR_SET_PDEATHSIG, with no exit accounting and no
        # metric shards retired.
        if disarm is not None:
            disarm()
        readiness_failed.set()
        supervisor = _pipeline_supervisor
        if supervisor is not None:
            supervisor.request_shutdown()

    if on_ready is not None:
        def announce_with_fleet_state() -> None:
            # Published before the line rather than on the next poll: the two
            # run off different clocks -- readiness fires the moment the last
            # child signals, the gauges are refreshed by the supervisor loop --
            # so anything reacting to the line would otherwise read a ready
            # count of zero for up to one poll interval.
            publish_fleet_state()
            on_ready()

        _announce_when_all_ready(ready_events, announce_with_fleet_state,
                                 on_timeout=lambda: on_readiness_timeout(disarm_watchdog),
                                 timeout=announce_timeout)

    _pipeline_supervisor = ProcessSupervisor(
        count=process_count,
        spawn=lambda index: _start_pipeline_process(
            config_path, index, process_count, ready_events[index],
            join_deadline,
        ),
        on_exit=_on_pipeline_process_exit,
        on_poll=publish_fleet_state,
        watch=watch,
    )
    try:
        # Re-checked here because the watchdog can expire between the two
        # statements, when there was no supervisor yet to ask to stop.
        if readiness_failed.is_set():
            _pipeline_supervisor.request_shutdown()
        _pipeline_supervisor.run()
    finally:
        _pipeline_supervisor = None

    if readiness_failed.is_set():
        raise RuntimeError(
            f"pipeline processes were not all ready within "
            f"{announce_timeout:.0f}s of the startup budget"
        )


def shutdown_exit_code() -> int:
    """0 for a shutdown somebody asked for, 1 for one the watchdog forced.

    The handler cannot tell them apart on its own -- both arrive as SIGTERM --
    so a startup that ran out of budget was reported as an operator stopping
    the container, which is a failure reported as a success.
    """
    failed_step = startup_deadline.expired_step()
    if failed_step is None:
        return 0
    logger.error("Exiting non-zero: startup timed out at %s", failed_step)
    return 1


def setup_signal_handlers(fastapi_process, on_shutdown=None):
    """Setup signal handlers for graceful shutdown in Docker containers.

    ``on_shutdown`` is run before anything is torn down. The startup watchdog
    is disarmed through it: a teardown can outlast the startup budget, and a
    watchdog firing inside this handler re-enters it, double-counting every
    child exit and abandoning the escalation half-done. Passed in rather than
    closed over, because this function is importable and the entry point's
    ExitStack is not.
    """

    def signal_handler(signum, frame):
        """Handle shutdown signals gracefully."""
        if on_shutdown is not None:
            try:
                on_shutdown()
            except Exception:
                # Never at the cost of the teardown it precedes: raising here
                # would strand the children and lose the exit code chosen
                # below, for the sake of a callback whose only job is to stop
                # a timer.
                logger.warning("Shutdown callback failed", exc_info=True)
        signal_name = signal.Signals(signum).name
        logger.info(f"Received {signal_name} signal, initiating graceful shutdown...")

        supervisor = _pipeline_supervisor
        if supervisor is not None:
            logger.info("Stopping pipeline processes...")
            try:
                # Before stop(), which reads the flag to decide whether the
                # exits it reaps were asked for. Without this every clean
                # shutdown is reported as a crash, which is the distinction
                # backwards.
                supervisor.request_shutdown()
                supervisor.stop()
            except Exception as e:
                logger.error(f"Error stopping pipeline processes: {e}")

        try:
            # Terminate FastAPI process
            if fastapi_process:
                if fastapi_process.is_alive():
                    logger.info("Terminating FastAPI server...")
                    fastapi_process.terminate()

                    # Wait for graceful shutdown with timeout
                    fastapi_process.join(timeout=10)

                    # Force kill if still alive
                    if fastapi_process.is_alive():
                        logger.warning("FastAPI server did not terminate gracefully, forcing shutdown...")
                        fastapi_process.kill()
                        fastapi_process.join()

                _mark_prometheus_process_dead(fastapi_process)
                logger.info("FastAPI server shutdown complete")

        except Exception as e:
            logger.error(f"Error during signal handler execution: {e}")
        finally:
            logger.info("Alert Bridge shutdown complete")
            # Non-zero when the startup watchdog raised this signal: a budget
            # that ran out is a failed start, and reporting it as a clean stop
            # hides it from the orchestrator and from the exit metrics.
            sys.exit(shutdown_exit_code())

    # Register signal handlers for Docker container management
    signal.signal(signal.SIGTERM, signal_handler)  # Docker stop
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    signal.signal(signal.SIGQUIT, signal_handler)  # Quit signal


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Alert Bridge - Anomaly Enhancement with HTTP API")
    parser.add_argument('--config', type=str,
                        default='config.yaml', help='Path to the config file.')
    args = parser.parse_args()

    # Propagate the --config path into CONFIG_PATH so the FastAPI
    # subprocess (spawned below) sees the same config file. Without
    # this, every `load_config()` call inside the FastAPI process —
    # including the always-on feature gate, the Elastic/Redis
    # dependency providers, and anything else reading `CONFIG_PATH` —
    # silently falls back to the default "config.yaml" in the CWD
    # and ignores the file the operator actually asked for. We set
    # this on the parent's os.environ *before* Process.start() so the
    # child inherits it, and use an absolute path so the child's cwd
    # cannot shift the lookup.
    os.environ["CONFIG_PATH"] = os.path.abspath(args.config)

    fastapi_process = None
    enhancer = None
    exit_code = 0
    exit_stack = contextlib.ExitStack()

    def start_api_child():
        """Bring the API child up, and take ownership of shutdown signals.

        Assigns ``fastapi_process`` as soon as the child exists, so a failure
        anywhere past this point still has something to tear down.
        """
        global fastapi_process

        fastapi_process = _pipeline_mp_context().Process(
            target=start_fastapi,
            args=(os.getpid(), shared_fleet_state),
            name="ab-fastapi",
        )
        fastapi_process.start()
        logger.info("FastAPI server started in separate process")

        # Setup signal handlers for graceful shutdown
        setup_signal_handlers(fastapi_process, on_shutdown=exit_stack.close)

    def require_api_child_alive():
        """Fail before committing to work that a dead endpoint would waste.

        Nothing watches the child until the supervisor starts, and on the
        single-process path nothing watches it at all -- so a port that was
        already taken would otherwise surface only after the instance had
        joined the consumer group and begun reading.
        """
        if fastapi_process is not None and not fastapi_process.is_alive():
            raise RuntimeError(
                f"the API child exited during startup "
                f"(exitcode={fastapi_process.exitcode})"
            )

    def bind_metrics_port():
        # Start Prometheus metrics server in main process (where metrics are recorded).
        if PROMETHEUS_ENABLED:
            # Materialize every labelled-counter series at value 0 before
            # the first scrape (C15). Counters are monotonic so inc(0) is
            # a no-op numerically, but it transforms "series absent" into
            # "series present with value 0" — which is what operators and
            # ``rate()`` queries actually expect from a freshly-started
            # process.
            warm_startup_labels()

            prometheus_port = int(os.getenv("PROMETHEUS_PORT", 9081))
            try:
                _start_prometheus_metrics_server(prometheus_port)
                logger.info(f"Prometheus metrics server started on port {prometheus_port}")
            except OSError as e:
                logger.error(f"Failed to start Prometheus server on port {prometheus_port}: {e}")
                logger.warning("Continuing without Prometheus metrics endpoint")

    try:
        # With alert_agent.processes > 1 the parent owns no pipeline at
        # all: each child builds its own enhancer, and therefore its own
        # consumers, event loop and clients. The parent must never
        # construct one — a parent that joined the consumer group and
        # then stopped polling would stall the partitions assigned to it.
        pipeline_config = AnomalyEnhancer.load_config(args.config)
        process_count = resolve_process_count(pipeline_config)
        source_partitions = None
        source_partition_sizes = None
        multi_process = process_count > 1
        # One budget for the whole of startup; each step below takes what the
        # steps before it left. Raise alert_agent.startup_timeout_seconds where
        # the default cannot be met -- a topic-creation Job racing this
        # Deployment, or several children contending on Elasticsearch.
        startup_budget = startup_timeout(pipeline_config)
        startup_began = time.monotonic()
        # The floor under every step, including the two that take no timeout:
        # the prompt store and the single-process pipeline are both built by
        # constructors that reach Elasticsearch with no way to bound them.
        # Steps that can take a deadline still do, so they fail naming
        # themselves; this catches whatever they miss.
        deadline = exit_stack.enter_context(StartupDeadline(startup_budget))

        def startup_remaining() -> float:
            return max(0.0, startup_budget - (time.monotonic() - startup_began))

        def fleet_join_budget() -> float:
            """What the budget has left for the group join.

            Reported, not padded. Refusing here used to turn a slow dependency
            into a crash loop, and padding to a floor pushed the whole of
            startup past the deadline it is measured against. The overrun is
            logged instead, which is the thing an operator can act on: the
            lever is alert_agent.startup_timeout_seconds.
            """
            remaining = startup_remaining()
            if remaining < MIN_FLEET_WAIT_SECONDS:
                logger.warning(
                    "Startup used %.0fs of its %.0fs budget before the "
                    "consumer-group join, leaving it %.0fs of the %.0fs a "
                    "join normally needs. Raise "
                    "alert_agent.startup_timeout_seconds.",
                    startup_budget - remaining, startup_budget, remaining,
                    MIN_FLEET_WAIT_SECONDS,
                )
            return remaining

        def require_startup_budget(step: str) -> None:
            """Fail rather than carry on past the budget with none left.

            Every wait draws from one budget, so a step that overran leaves
            the next with nothing. Saying so here names the step that spent it
            instead of surfacing later as a readiness timeout of zero seconds.
            """
            if startup_remaining() <= 0:
                raise RuntimeError(
                    f"startup exceeded alert_agent.startup_timeout_seconds="
                    f"{startup_budget:.0f}s at {step}"
                )

        enforce_log_level(args.config)
        validate_drain_fits_poll_interval(pipeline_config)

        # Where the count is first known, and before the endpoints open, so
        # the first probe can already tell "no pipeline yet" from "no pipeline
        # needed". Unconditional: unlike the gauges below, this one is not
        # optional -- it is what /health reads when metrics are off, which is
        # the default.
        shared_fleet_state = fleet_state.create(_pipeline_mp_context(), process_count)
        if PROMETHEUS_ENABLED:
            from metrics.recorder import set_pipeline_process_counts
            set_pipeline_process_counts(process_count, 0, 0)

        # Local configuration is judged before anything is started. These are
        # pure predicates over the config with no I/O, so a wrong value fails
        # the container without ever binding a port or forking a child.
        if not EventBridgeFactory.validate_configuration(pipeline_config):
            raise ValueError("Invalid event bridge configuration")
        # Judged here, not where the consumer is built: that is inside a child,
        # after the API, the metrics port, the metadata wait, seeding, warmup
        # and the fork -- so a one-line config mistake crash-looped children
        # instead of failing before anything started.
        # Imported here rather than at module level: the wiring tests stub
        # sys.modules, so a new top-level import of an mdx module breaks them.
        from mdx.kafka_message_broker import _require_eager_assignor
        _require_eager_assignor(
            (pipeline_config.get("kafka", {}) or {}).get("partition_assignment_strategy")
        )
        if multi_process:
            validate_multi_process_config(pipeline_config, process_count)

        # Only then the endpoints, and before anything that blocks: the wait
        # for topic metadata below, and the enhancer constructor, which
        # contends on Elasticsearch for tens of seconds. Starting the endpoint
        # after either left port 9080 answering nothing for exactly that
        # window, so a startup probe killed the container first.
        deadline.step("starting the API child")
        start_api_child()

        if multi_process:
            bind_metrics_port()

        # Blocks until the topics exist, then raises if they carry fewer
        # partitions than there are processes. Waiting is what lets the same
        # check hold on both deployment paths: Compose starts this only after
        # the topic-init container completes, while on Kubernetes the topics
        # come from a Job that races this Deployment.
        #
        # Run at one process too, but not for the same reason: at N=1 the
        # partition-count check is trivially satisfied, so what this buys is
        # noticing missing topics early. It reports and proceeds there rather
        # than refusing, because a broker with auto-create recovers on its own
        # and refusing would break installs that work today.
        deadline.step("reading source topic metadata")
        # Bounded by what the fleet still needs. Given the whole budget, the
        # one-process path reached its "report and continue" branch exactly as
        # the watchdog expired, so the tolerance it exists for was unreachable
        # and a briefly unreadable broker crash-looped the default install.
        source_partition_sizes = await_source_partitions(
            pipeline_config, process_count,
            timeout=max(0.0, startup_remaining() - MIN_FLEET_WAIT_SECONDS),
        )
        source_partitions = sum(source_partition_sizes.values())

        if multi_process:

            # Checked before the seeding and the fork, so a dead endpoint costs
            # the wait above and nothing more -- rather than being noticed on
            # the supervisor's first poll, after N children have joined the
            # group and started reading.
            require_api_child_alive()

            # Seed the prompt store here, before a child exists to consume
            # against it. Only the seeding process wrote it before, and it
            # wrote while building its own pipeline, so the children that
            # skipped that write finished building first and could start
            # reading a store nobody had filled. Fatal on failure: an event
            # with no prompt is dropped, so serving traffic without a
            # confirmed store is worse than not starting.
            deadline.step("seeding the prompt store")
            seed_prompt_store(args.config)
            require_startup_budget("prompt seeding")

        if not multi_process:
            # Constructed before the metrics port binds (C15): the constructor
            # populates state that scrapes should see from the very first
            # response, and a boot that fails here must never expose a
            # "healthy" metrics endpoint. The API child is already up; it keeps
            # its own registry, so it is not bound by this.
            deadline.step("building the pipeline")
            enhancer = AnomalyEnhancer(args.config)
            # Published before the port opens, so the first probe already sees
            # one pipeline configured and none ready. Left unpublished, /ready
            # read "nothing configured" as nothing wrong and answered 200 for
            # the whole of startup.
            enhancer._publish_assignment_state()
            bind_metrics_port()

        if os.environ.get("VLM_WARMUP_ENABLED", "true").lower() != "false":
            video_path = WARMUP_VIDEO if os.path.isfile(WARMUP_VIDEO) else "warmup/test.mp4"
            if not os.path.isfile(video_path):
                logger.warning("Warmup video not found at %s, skipping VLM warmup", video_path)
            else:
                # Warm up once in the parent, before forking, so N children do
                # not each pay for (and each measure) a cold backend.
                warmup_config = (
                    enhancer.vlm_client.config if enhancer else pipeline_config.get('vlm', {})
                )
                # Capped by what the budget has left, less the fleet's share.
                # Warmup polls a backend that may still be loading weights and
                # has its own five-minute patience; left outside the budget it
                # could spend the whole of startup and leave the check after it
                # to fail a container that was only waiting for a dependency.
                # A skipped warmup is already tolerated below; a refused
                # container is not.
                # One deadline for the whole of warmup, passed straight in.
                # Carving the reservation up per knob instead made
                # inference_timeout about two seconds against a documented
                # hundred and twenty, so warmup could never succeed and
                # quietly warmed nothing -- and a cold start that did not grow
                # read as proof it cost nothing. The configured knobs are left
                # alone; warmup_vlm bounds itself against the deadline.
                warmup_budget = max(0.0, startup_remaining() - MIN_FLEET_WAIT_SECONDS)
                if warmup_budget <= 0:
                    logger.warning(
                        "Skipping VLM warmup: the startup budget is spent. A "
                        "cold backend costs a slower first inference, not a "
                        "refused container."
                    )
                else:
                    deadline.step("warming up the VLM")
                    try:
                        warmup_vlm(warmup_config, video_path=video_path,
                                   deadline=time.monotonic() + warmup_budget)
                    except Exception:
                        logger.warning("VLM warmup failed -- continuing without warmup", exc_info=True)
        else:
            logger.info("VLM warmup disabled via VLM_WARMUP_ENABLED=false")

        def announce_ready() -> None:
            # Canonical readiness line: health checks and the functional-test
            # harness gate on it, so both branches must emit it exactly once --
            # and only once the source can receive what a producer sends next.
            # Kafka consumers are built on first read and join their group
            # asynchronously, so announcing any earlier invites a producer to
            # write past a `latest` offset no member has reached.
            deadline.step("running")
            # The deadline directly, not the shared ExitStack: this runs on
            # the readiness thread while the main thread may be closing the
            # same stack, and ExitStack pops its callbacks without a lock.
            deadline.__exit__(None, None, None)
            logger.info("Starting anomaly processing loop...")

        if multi_process:
            # Per topic, not the sum: each topic is assigned independently, so
            # the sum reads as headroom that raising the count cannot use.
            logger.info(
                "Pipeline running across %d processes over %s",
                process_count,
                ", ".join(f"{topic}={size}" for topic, size in sorted(
                    (source_partition_sizes or {}).items())) or
                f"{source_partitions} partitions",
            )
            deadline.step("waiting for the pipeline processes to join")
            fleet_wait = fleet_join_budget()
            # The parent's announce window and the child's floored join both
            # run past the plain remainder, so the watchdog has to cover the
            # same ground. Extended unconditionally, not only when the budget
            # ran short: otherwise it fires exactly MIN_FLEET_WAIT_SECONDS
            # before the window it was widened for, which made the widening
            # inert and left the per-child readiness diagnostic unreachable.
            _, watchdog_extension = readiness_windows(fleet_wait)
            deadline.extend(watchdog_extension)
            run_multi_process_pipeline(os.path.abspath(args.config), process_count,
                                       on_ready=announce_ready,
                                       watch=[fastapi_process],
                                       readiness_timeout=fleet_wait,
                                       disarm_watchdog=lambda: deadline.__exit__(
                                           None, None, None))
        else:
            # Note: at one process there is no supervisor, so the API child is
            # not watched. A dead endpoint there leaves the pipeline running
            # without one -- the same state the watch exists to prevent above.
            # Closing that means giving the single-process path a supervisor,
            # which is a larger change than this review round.
            # Labelled, or a timeout here is reported against whatever step
            # ran before it -- warmup, or building the pipeline.
            deadline.step("waiting for the pipeline to join its consumer group")
            if not enhancer.source.await_ready(timeout=fleet_join_budget()):
                raise RuntimeError("source could not join the consumer group")
            require_api_child_alive()
            announce_ready()
            if not enhancer.process_anomalies():
                # A supervised child fails its instance by exiting; a lone
                # pipeline has to say so itself, or the loop stops and the
                # orchestrator is told the container finished successfully.
                raise RuntimeError("the consume loop stopped on an error")

    except KeyboardInterrupt:
        # This handles Ctrl+C when not in Docker (development)
        logger.info("Received KeyboardInterrupt, shutting down Alert Bridge...")

    except SystemExit as e:
        # Handle sys.exit() calls
        logger.info("Received SystemExit, shutting down Alert Bridge...")
        exit_code = e.code if isinstance(e.code, int) else 0

    except Exception as e:
        # Handle any other unexpected exceptions
        logger.error(f"Unexpected error in main process: {e}", exc_info=True)
        # An error here is not a clean stop: the supervisor gives up like this
        # when a slot cannot stay alive, and reporting success would let an
        # orchestrator treat a crash-looping deployment as a finished job.
        exit_code = 1

    finally:
        # Cleanup code that always runs
        # Stops the startup watchdog on every exit, including the failure
        # paths: a live timer would otherwise signal a process already on its
        # way down.
        exit_stack.close()
        logger.info("Performing final cleanup...")

        try:
            # Close enhancer resources
            if enhancer:
                logger.info("Closing anomaly enhancer resources...")
                if hasattr(enhancer, 'source') and enhancer.source:
                    enhancer.source.close()
                if hasattr(enhancer, 'sink') and enhancer.sink:
                    enhancer.sink.close()

        except Exception as e:
            logger.warning(f"Error during enhancer cleanup: {e}")

        try:
            # Cleanup FastAPI process
            if fastapi_process:
                if fastapi_process.is_alive():
                    logger.info("Terminating FastAPI server...")
                    fastapi_process.terminate()

                    # Wait for graceful shutdown with timeout
                    fastapi_process.join(timeout=10)

                    # Force kill if still running
                    if fastapi_process.is_alive():
                        logger.warning("FastAPI server did not terminate gracefully, forcing shutdown...")
                        fastapi_process.kill()
                        fastapi_process.join()

                _mark_prometheus_process_dead(fastapi_process)
                logger.info("FastAPI server shutdown complete")

        except Exception as e:
            logger.warning(f"Error during FastAPI cleanup: {e}")

        logger.info("Alert Bridge shutdown complete")

    if exit_code:
        sys.exit(exit_code)

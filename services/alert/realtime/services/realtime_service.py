#!/usr/bin/env python3
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

"""
Service layer for managing real-time VLM (RTVI) alert rules.
"""

import asyncio
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

import httpx

from ..config import ErrorCode, ResponseStatus, RuleStatus, load_config
from ..schemas import (
    AlertRuleConfig,
    EXTENDED_OPTIONAL_FIELDS,
    STREAM_IDENTITY_OPTIONAL_FIELDS,
)
from .rtvi_client import RTVIVLMClient


if TYPE_CHECKING:
    from .rule_store import RuleStore

logger = logging.getLogger(__name__)

try:
    from metrics import PROMETHEUS_ENABLED
    if PROMETHEUS_ENABLED:
        from metrics.prometheus_metrics import (
            REALTIME_RULES_ACTIVE,
            REALTIME_RULES_COUNT,
            REALTIME_RULES_CREATED,
            REALTIME_RULES_DELETED,
            REALTIME_RULES_FAILED,
            REALTIME_RULES_PERSISTED,
            REPLAY_INVOCATIONS,
            REPLAY_RULE_FAILURES,
            RTVI_CALL_DURATION,
            RTVI_CALL_FAILURES,
        )
    else:
        REALTIME_RULES_ACTIVE = None
        REALTIME_RULES_COUNT = None
        REALTIME_RULES_CREATED = None
        REALTIME_RULES_DELETED = None
        REALTIME_RULES_FAILED = None
        REALTIME_RULES_PERSISTED = None
        REPLAY_INVOCATIONS = None
        REPLAY_RULE_FAILURES = None
        RTVI_CALL_DURATION = None
        RTVI_CALL_FAILURES = None
except ImportError:
    PROMETHEUS_ENABLED = False
    REALTIME_RULES_ACTIVE = None
    REALTIME_RULES_COUNT = None
    REALTIME_RULES_CREATED = None
    REALTIME_RULES_DELETED = None
    REALTIME_RULES_FAILED = None
    REALTIME_RULES_PERSISTED = None
    REPLAY_INVOCATIONS = None
    REPLAY_RULE_FAILURES = None
    RTVI_CALL_DURATION = None
    RTVI_CALL_FAILURES = None


_INTERNAL_FIELDS = frozenset({
    "rtvi_stream_id", "previous_rtvi_stream_id", "owns_rtvi_stream",
    "_id", "_index", "_seq_no", "_primary_term",
})


class _StreamReadinessError(Exception):
    """Raised by :meth:`_wait_stream_ready` when the captions task fails
    *after* the initial ack window — i.e. during the get-stream-info
    readiness probe. Lets the caller distinguish "RTVI rejected the
    request outright" (ack-phase ``httpx.HTTPError`` propagates as-is)
    from "RTVI accepted the call but the stream itself isn't readable"
    so the two cases can map to ``RTVI_VLM_UNAVAILABLE`` vs
    ``RTVI_STREAM_NOT_READABLE`` respectively.
    """

    def __init__(self, cause: BaseException):
        super().__init__(str(cause))
        self.cause = cause


class _StreamIdentityConflict(Exception):
    """Raised by :meth:`_resolve_or_add_stream` when the existing RTVI
    stream registered under ``sensor_id`` points at a different
    ``liveStreamUrl`` than the request asks for.

    Reusing such a stream would silently bind the new rule to an
    unrelated camera while persisting the *requested* URL on the rule
    document, and a later last-reader delete could tear down a stream
    this service never created. Surface the mismatch as a 409 so the
    caller can either pick a fresh ``sensor_id`` or reconcile with the
    existing registration.
    """

    def __init__(
        self,
        *,
        sensor_id: str,
        requested_url: str,
        existing_url: str,
    ) -> None:
        super().__init__(
            "sensor_id is already registered with a different stream URL"
        )
        self.sensor_id = sensor_id
        self.requested_url = requested_url
        self.existing_url = existing_url


def _observe(histogram, method: str, duration: float) -> None:
    if histogram is not None:
        histogram.labels(method=method).observe(duration)


def _inc_failure(counter, method: str) -> None:
    if counter is not None:
        counter.labels(method=method).inc()


def _inc_stage_failure(counter, stage: str) -> None:
    if counter is not None:
        counter.labels(stage=stage).inc()


def _inc(counter) -> None:
    """Null-safe ``.inc()`` for unlabelled counters."""
    if counter is not None:
        counter.inc()


def _set_gauge(gauge, value: float) -> None:
    """Null-safe ``.set()`` for unlabelled gauges."""
    if gauge is not None:
        gauge.set(value)


def _inc_replay_outcome(counter, outcome: str) -> None:
    """Null-safe inc for ``REPLAY_INVOCATIONS{outcome=...}``."""
    if counter is not None:
        counter.labels(outcome=outcome).inc()


class RealtimeAlertService:
    """
    Manages the lifecycle of real-time VLM alert rules.

    When constructed with a :class:`~.rule_store.RuleStore`, every write
    goes through Elasticsearch first (persist-first) so rules survive
    restarts and Elasticsearch becomes the system of record.  When
    ``rule_store`` is ``None`` the service falls back to the legacy
    in-memory registry — this path is kept so
    :class:`~.always_on_service.AlwaysOnService` and existing tests
    continue to work without requiring an ES cluster.

    Lifecycle of ``start_alert`` (persistent path):

    1. Write the rule to Elasticsearch with ``status=pending``.
    2. Resolve the RTVI stream via :meth:`_resolve_or_add_stream`. When
       the request carries a ``sensor_id``, ``GET /streams/get-stream-info``
       is consulted first and a matching registration (``id == sensor_id``)
       is reused — no new ``/streams/add`` is issued. The
       ``owns_rtvi_stream`` flag records whether this rule was the one
       that added the stream and is preserved as diagnostic metadata.
       On RTVI HTTP error → delete the ES record → return 502.
       On missing stream id → delete the ES record → return 502.
    3. Trigger caption generation and call :meth:`_wait_stream_ready`,
       which waits up to ``captions_ack_timeout`` for the initial HTTP
       ack and — for newly-added streams only — polls
       ``GET /streams/get-stream-info`` every
       ``stream_readiness_poll_interval`` seconds (capped at
       ``stream_readiness_max_wait``) to confirm the stream is visible.
       Reused streams skip the probe entirely.
       Ack-phase ``httpx.HTTPError`` → ``RTVI_VLM_UNAVAILABLE`` 502;
       readiness-phase failure → ``RTVI_STREAM_NOT_READABLE`` 502.
    4. Update the ES record with ``rtvi_stream_id``, ``status=active``,
       and the resolved ``owns_rtvi_stream`` flag.
    5. Commit the rule to the in-memory registry and return 201.

    Lifecycle of ``stop_alert`` uses live ref-count semantics: after
    deleting the rule from ES / the in-memory registry, count *other*
    rules referencing the same ``rtvi_stream_id``. If the count is
    zero the deleted rule was the last reader, so both
    ``stop_captions`` and ``stop_stream`` fire. Otherwise only
    ``stop_captions`` runs so siblings keep streaming. The same logic
    applies to background ``_cleanup_failed_rule`` calls; ``start_alert``
    rollbacks additionally include ``PENDING`` rules in the count so a
    concurrent in-flight create isn't accidentally torn down.
    """

    def __init__(
        self,
        config_file: str = "config.yaml",
        rule_store: Optional["RuleStore"] = None,
    ):
        self._config = load_config(config_file)

        rtvi_cfg = self._config.get("rtvi_vlm", {})
        base_url = rtvi_cfg.get(
            "base_url",
            os.getenv("RTVI_VLM_BASE_URL", "http://localhost:8000"),
        )
        timeout = rtvi_cfg.get("timeout", 30)
        self._default_model = rtvi_cfg.get("default_model", "")
        self._captions_ack_timeout = rtvi_cfg.get("captions_ack_timeout", 2.0)
        # Readiness now polls ``/streams/get-stream-info`` (cheap GET) instead
        # of holding the response open for the full caption-task ack window.
        # ``stream_readiness_max_wait`` caps the total polling time; the loop
        # sleeps ``stream_readiness_poll_interval`` between probes. Defaults
        # are tuned so a healthy stream confirms in well under a second while
        # an unreachable RTVI fails fast.
        self._stream_readiness_poll_interval = rtvi_cfg.get(
            "stream_readiness_poll_interval", 0.5,
        )
        self._stream_readiness_max_wait = rtvi_cfg.get(
            "stream_readiness_max_wait", 2.0,
        )
        # ``stream_readiness_timeout`` is deprecated — kept only so existing
        # configs don't fail to load. The polling helper ignores it.
        if "stream_readiness_timeout" in rtvi_cfg:
            logger.warning(
                "rtvi_vlm.stream_readiness_timeout is deprecated and ignored; "
                "use stream_readiness_max_wait + stream_readiness_poll_interval"
            )

        self._client = RTVIVLMClient(base_url=base_url, timeout=timeout)
        self._rule_store = rule_store

        self._lock = threading.Lock()
        self._rules: Dict[str, Dict[str, Any]] = {}
        self._caption_tasks: Set[asyncio.Task] = set()
        self._readiness_cleaned_streams: Set[str] = set()
        # alert_rule_ids for which readiness cleanup has fired but start_alert
        # has not yet aborted — lets the create path detect and undo a racing
        # ES ACTIVE write before committing the rule to _rules.
        self._readiness_failed_ids: Set[str] = set()
        # In-flight create refs keyed by ``rtvi_stream_id`` → set of
        # ``alert_rule_id`` currently mid-create on that stream. Populated
        # right after :meth:`_resolve_or_add_stream` returns and cleared
        # once the rule has been durably committed (or fully rolled back).
        # Bridges the gap left by ``_build_rule_doc`` writing the PENDING
        # ES row before ``rtvi_stream_id`` is known: rollback paths use
        # this set to recognise concurrent siblings reusing the same
        # stream, so a rolling-back owner can't tear the stream out from
        # under a sibling that hasn't yet reached the ACTIVE update.
        self._pending_stream_refs: Dict[str, Set[str]] = {}
        # Async callbacks invoked when a rule is permanently removed by
        # readiness cleanup or a late caption-task failure.  Each is called
        # with the alert_rule_id string.
        self._rule_removed_callbacks: List[Callable[[str], Coroutine[Any, Any, None]]] = []
        self._replay_lock = asyncio.Lock()
        self._replaying: bool = False
        # Per-``sensor_id`` locks serialise the get-stream-info → maybe add
        # critical section so concurrent ``start_alert`` calls (notably the
        # always-on fan-out which fires N rules per camera in parallel) can't
        # race past the existence check and each issue their own
        # ``/streams/add`` for the same ``sensor_id``. The dict itself is
        # protected by ``self._lock`` because Python dict mutation isn't
        # async-safe even though each lookup is O(1).
        self._sensor_locks: Dict[str, asyncio.Lock] = {}

        logger.info(
            "RealtimeAlertService initialized",
            extra={
                "rtvi_vlm_base_url": base_url,
                "default_model": self._default_model,
                "persistent": rule_store is not None,
                "captions_ack_timeout": self._captions_ack_timeout,
                "stream_readiness_poll_interval": self._stream_readiness_poll_interval,
                "stream_readiness_max_wait": self._stream_readiness_max_wait,
            },
        )

    async def aclose(self) -> None:
        """Cancel long-running caption tasks and close the HTTP client."""
        for task in list(self._caption_tasks):
            task.cancel()
        await self._client.aclose()

    def register_rule_removed_callback(
        self, callback: Callable[[str], Coroutine[Any, Any, None]]
    ) -> None:
        """Register an async callback invoked when a rule is permanently removed.

        The callback receives the ``alert_rule_id`` string.  It is scheduled
        as a fire-and-forget task so it must not raise unhandled exceptions.
        """
        self._rule_removed_callbacks.append(callback)

    # ------------------------------------------------------------------
    # Re-onboard (shared by replay)
    # ------------------------------------------------------------------

    async def _re_onboard_rule(
        self,
        rule_id: str,
        rule_doc: Dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> str:
        """Re-onboard a single rule onto RTVI VLM and persist the result.

        Contract: call RTVI start_stream + generate_captions; on success
        write exactly one ES update (``rtvi_stream_id`` + ``last_replay_at``);
        on failure mark the ES record ``FAILED`` and fire rule-removed
        callbacks so the always-on sidecar stays consistent.

        Precondition: ``self._rule_store`` must not be ``None``.

        ``correlation_id`` (when provided by :meth:`_do_replay`) is woven
        into every log line for this rule so operators can grep one
        replay invocation end-to-end across the per-rule fan-out.

        Returns the new ``rtvi_stream_id``.
        """
        if self._rule_store is None:
            raise RuntimeError(
                "_re_onboard_rule requires a configured rule_store"
            )
        model = rule_doc.get("model") or self._default_model
        # ``sensor_id`` is optional in ``AlertRuleConfig``; reads from the
        # ES doc default to ``None`` so legacy documents (written before
        # ``sensor_id`` existed) still replay — the RTVI client forwards
        # ``None`` as ``null`` and lets RTVI generate its own stream id.
        config = AlertRuleConfig(
            live_stream_url=rule_doc["live_stream_url"],
            alert_type=rule_doc["alert_type"],
            prompt=rule_doc["prompt"],
            sensor_id=rule_doc.get("sensor_id"),
            sensor_name=rule_doc.get("sensor_name"),
            description=rule_doc.get("description"),
            username=rule_doc.get("username"),
            password=rule_doc.get("password"),
            place_name=rule_doc.get("place_name"),
            place_type=rule_doc.get("place_type"),
            place_lat=rule_doc.get("place_lat"),
            place_lon=rule_doc.get("place_lon"),
            place_alt=rule_doc.get("place_alt"),
            place_coordinate_x=rule_doc.get("place_coordinate_x"),
            place_coordinate_y=rule_doc.get("place_coordinate_y"),
            system_prompt=rule_doc.get("system_prompt", ""),
            model=model,
            chunk_duration=rule_doc.get("chunk_duration", 30),
            chunk_overlap_duration=rule_doc.get("chunk_overlap_duration", 5),
            num_frames_per_second_or_fixed_frames_chunk=rule_doc.get(
                "num_frames_per_second_or_fixed_frames_chunk", 10,
            ),
            use_fps_for_chunking=rule_doc.get("use_fps_for_chunking", True),
            vlm_input_width=rule_doc.get("vlm_input_width", 256),
            vlm_input_height=rule_doc.get("vlm_input_height", 256),
            enable_reasoning=rule_doc.get("enable_reasoning", True),
            api_type=rule_doc.get("api_type"),
            response_format=rule_doc.get("response_format"),
            stream_options=rule_doc.get("stream_options"),
            max_tokens=rule_doc.get("max_tokens"),
            temperature=rule_doc.get("temperature"),
            top_p=rule_doc.get("top_p"),
            top_k=rule_doc.get("top_k"),
            ignore_eos=rule_doc.get("ignore_eos"),
            seed=rule_doc.get("seed"),
            media_info=rule_doc.get("media_info"),
            enable_audio=rule_doc.get("enable_audio"),
            mm_processor_kwargs=rule_doc.get("mm_processor_kwargs"),
        )

        # Forward the full identity / location payload here just like
        # :meth:`start_alert` so replayed rules land on RTVI with the
        # same metadata operators originally posted. ``sensor_id`` and
        # ``sensor_name`` may be ``None`` (legacy ES doc) — the RTVI
        # client tolerates that by forwarding ``null`` and letting RTVI
        # apply its own defaults.
        #
        # Routes through :meth:`_resolve_or_add_stream` so a partial
        # earlier replay that already pushed the stream to RTVI is
        # detected via ``GET /streams/get-stream-info`` and reused
        # instead of re-issuing ``/streams/add`` (which would mint a
        # second registration for the same id and leak the original).
        replay_ctx: Dict[str, Any] = {
            "alert_rule_id": rule_id,
            "alert_type": config.alert_type,
            "correlation_id": correlation_id,
            "stage": "replay_rule",
        }
        try:
            rtvi_stream_id, owns_stream = await self._resolve_or_add_stream(
                config, replay_ctx,
            )
        except Exception:
            # start_stream failed or returned no usable stream id.
            # Mark the ES record FAILED so it doesn't remain ACTIVE/PENDING
            # and fire rule-removed callbacks to keep sidecars consistent.
            await self._mark_rule_failed(rule_id)
            for cb in self._rule_removed_callbacks:
                asyncio.create_task(cb(rule_id))
            raise

        replay_ctx["rtvi_stream_id"] = rtvi_stream_id
        replay_ctx["owns_stream"] = owns_stream

        # Replay re-onboards rules in parallel via ``asyncio.gather``,
        # so the same in-flight race that ``start_alert`` guards against
        # applies here: register the pending stream ref before
        # ``generate_captions`` so a sibling rolling back can see us.
        self._register_pending_stream_ref(rtvi_stream_id, rule_id)
        try:
            captions_task = asyncio.create_task(
                self._client.generate_captions(
                    stream_id=rtvi_stream_id,
                    prompt=config.prompt,
                    model=model,
                    system_prompt=config.system_prompt,
                    chunk_duration=config.chunk_duration,
                    chunk_overlap_duration=config.chunk_overlap_duration,
                    alert_category=config.alert_type,
                    num_frames_per_second_or_fixed_frames_chunk=config.num_frames_per_second_or_fixed_frames_chunk,
                    use_fps_for_chunking=config.use_fps_for_chunking,
                    vlm_input_width=config.vlm_input_width,
                    vlm_input_height=config.vlm_input_height,
                    enable_reasoning=config.enable_reasoning,
                    api_type=config.api_type,
                    response_format=config.response_format,
                    stream_options=config.stream_options,
                    max_tokens=config.max_tokens,
                    temperature=config.temperature,
                    top_p=config.top_p,
                    top_k=config.top_k,
                    ignore_eos=config.ignore_eos,
                    seed=config.seed,
                    media_info=config.media_info,
                    enable_audio=config.enable_audio,
                    mm_processor_kwargs=config.mm_processor_kwargs,
                )
            )
            try:
                await self._wait_stream_ready(
                    captions_task, rtvi_stream_id, owns_stream, replay_ctx,
                )
            except Exception:
                self._readiness_cleaned_streams.add(rtvi_stream_id)
                await self._maybe_stop_stream_on_rollback(
                    rtvi_stream_id, rule_id, owns_stream, replay_ctx,
                )
                await self._mark_rule_failed(rule_id)
                for cb in self._rule_removed_callbacks:
                    asyncio.create_task(cb(rule_id))
                raise

            if not captions_task.done():
                self._caption_tasks.add(captions_task)
                captions_task.add_done_callback(self._caption_tasks.discard)
                captions_task.add_done_callback(
                    lambda t, sid=rtvi_stream_id, rid=rule_id, svc=self: svc._log_caption_task_result(t, sid, rid)
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            try:
                await asyncio.to_thread(
                    self._rule_store.update, rule_id, {
                        "rtvi_stream_id": rtvi_stream_id,
                        "last_replay_at": now_iso,
                        "status": RuleStatus.ACTIVE,
                        "owns_rtvi_stream": owns_stream,
                    },
                )
            except Exception:
                logger.error(
                    "ES update failed after starting new RTVI stream — "
                    "rolling back stream %s to avoid orphan",
                    rtvi_stream_id,
                    extra={
                        "alert_rule_id": rule_id,
                        "rtvi_stream_id": rtvi_stream_id,
                        "correlation_id": correlation_id,
                        "stage": "replay_rule",
                        "outcome": "failure",
                    },
                    exc_info=True,
                )
                await self._maybe_stop_stream_on_rollback(
                    rtvi_stream_id, rule_id, owns_stream, replay_ctx,
                )
                await self._mark_rule_failed(rule_id)
                for cb in self._rule_removed_callbacks:
                    asyncio.create_task(cb(rule_id))
                raise

            rule = {
                "id": rule_id,
                "rtvi_stream_id": rtvi_stream_id,
                "owns_rtvi_stream": owns_stream,
                "live_stream_url": config.live_stream_url,
                "alert_type": config.alert_type,
                "sensor_id": config.sensor_id,
                "sensor_name": config.sensor_name,
                "prompt": config.prompt,
                "system_prompt": config.system_prompt,
                "model": model,
                "chunk_duration": config.chunk_duration,
                "chunk_overlap_duration": config.chunk_overlap_duration,
                "num_frames_per_second_or_fixed_frames_chunk": config.num_frames_per_second_or_fixed_frames_chunk,
                "use_fps_for_chunking": config.use_fps_for_chunking,
                "vlm_input_width": config.vlm_input_width,
                "vlm_input_height": config.vlm_input_height,
                "enable_reasoning": config.enable_reasoning,
                "status": RuleStatus.ACTIVE,
                "created_at": rule_doc.get("created_at", ""),
            }
            # Carry the stream-identity / location metadata into the
            # in-memory registry too — GET /api/v1/realtime falls back to
            # this dict when the persistent listing path isn't in use, so
            # the same fields that survived ES must also reach the
            # public response.
            for _field in STREAM_IDENTITY_OPTIONAL_FIELDS:
                _val = getattr(config, _field, None)
                if _val is not None:
                    rule[_field] = _val
            for _field in EXTENDED_OPTIONAL_FIELDS:
                _val = getattr(config, _field, None)
                if _val is not None:
                    rule[_field] = _val
            with self._lock:
                already_tracked = rule_id in self._rules
                self._rules[rule_id] = rule

            if REALTIME_RULES_ACTIVE is not None and not already_tracked:
                REALTIME_RULES_ACTIVE.inc()

            logger.info(
                "Re-onboarded rule %s (new rtvi_stream_id=%s)", rule_id, rtvi_stream_id,
                extra={
                    "alert_rule_id": rule_id,
                    "rtvi_stream_id": rtvi_stream_id,
                    "alert_type": config.alert_type,
                    "correlation_id": correlation_id,
                    "stage": "replay_rule",
                    "outcome": "success",
                },
            )
            return rtvi_stream_id
        finally:
            self._unregister_pending_stream_ref(rtvi_stream_id, rule_id)

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    @property
    def is_replaying(self) -> bool:
        return self._replaying

    async def replay(self) -> Tuple[Dict[str, Any], int]:
        """Replay all persisted active, failed, or pending rules onto RTVI VLM.

        Returns ``(response_dict, status_code)``.

        * 409 — a replay is already in flight on this instance.
        * 501 — persistence not configured.
        * 200 — replay completed (``details`` array has per-rule outcomes).

        Concurrency is guarded by a process-local ``asyncio.Lock``.
        Multi-replica deployments must use an external coordinator
        (e.g. Kubernetes leader election) to ensure only one replica
        replays at a time.
        """
        # One UUID per replay invocation, woven through every log line
        # so operators can grep one invocation end-to-end (start,
        # per-rule outcomes, end). Generated *before* any short-circuit
        # so the 501 / 409 / 502 error responses (and their log lines)
        # also carry an id that the caller can grep on.
        correlation_id = uuid.uuid4().hex

        if self._rule_store is None:
            # Intentionally NOT incrementing ``REPLAY_INVOCATIONS``:
            # 501 short-circuits never start an actual replay run, and
            # mixing them into the invocations counter inflates the
            # "real replay activity" rate that operators alert on (see
            # the maintainer review).  The
            # ``replay_end`` log line below is the canonical signal
            # that a 501 short-circuit happened.
            logger.info(
                "Replay rejected: realtime persistence disabled",
                extra={
                    "correlation_id": correlation_id,
                    "stage": "replay_end",
                    "outcome": "skipped_disabled",
                },
            )
            return self._error_response(
                code=501,
                error=ErrorCode.PERSISTENCE_DISABLED,
                message=(
                    "Replay requires realtime persistence (Elasticsearch) "
                    "which is disabled in the current configuration. Enable "
                    "both 'persistence.enabled' and "
                    "'rtvi_vlm.enable_realtime_persistence' and restart to "
                    "use replay."
                ),
                correlation_id=correlation_id,
            )

        if self._replay_lock.locked():
            # Same rationale as the 501 path above (Finding 3): 409
            # short-circuits don't represent an actual replay run, so
            # they are tracked via ``replay_end`` log lines instead of
            # the ``REPLAY_INVOCATIONS`` counter.
            logger.info(
                "Replay rejected: another replay is already in flight",
                extra={
                    "correlation_id": correlation_id,
                    "stage": "replay_end",
                    "outcome": "skipped_in_flight",
                },
            )
            return self._error_response(
                code=409,
                error="replay_in_flight",
                message="A replay is already in progress on this instance",
                correlation_id=correlation_id,
            )

        # Acquire + set _replaying atomically (no await between).
        # asyncio.Lock.acquire() is synchronous when the lock is free,
        # so no other coroutine can interleave between acquire and flag set.
        await self._replay_lock.acquire()
        self._replaying = True
        try:
            return await self._do_replay(correlation_id)
        finally:
            self._replaying = False
            self._replay_lock.release()

    async def _do_replay(
        self, correlation_id: str,
    ) -> Tuple[Dict[str, Any], int]:
        """Re-onboard every active, failed, or pending rule in ES onto RTVI VLM.

        This method does exactly one thing per rule: call RTVI to
        start a new stream + captions, then persist the new
        ``rtvi_stream_id`` and ``last_replay_at`` in a single ES
        write.  On failure the ES record is marked ``FAILED``.

        Pending rules are included because they represent records that
        were persisted (persist-first) but never activated — typically
        because the process crashed between ES write and RTVI
        confirmation.  Skipping them would leave crash-orphaned rules
        unrecoverable.
        """
        # ``outcome="started"`` keeps the log shape consistent with the
        # documented contract (every replay log line carries
        # ``correlation_id``, ``stage`` and ``outcome``) so dashboards
        # filtering on ``outcome`` don't need a special-case for the
        # start record.  See spec ``realtime_vlm_alerts.md`` → "Replay
        # correlation id".
        replay_ctx = {
            "correlation_id": correlation_id,
            "stage": "replay_start",
            "outcome": "started",
        }
        logger.info("Replay: started", extra=replay_ctx)

        try:
            result = await asyncio.to_thread(
                self._rule_store.list, size=10_000,
            )
        except Exception as exc:
            logger.error(
                "Replay failed — cannot read rules from ES: %s", exc,
                extra={
                    "correlation_id": correlation_id,
                    "stage": "replay_end",
                    "outcome": "failure",
                },
            )
            _inc_replay_outcome(REPLAY_INVOCATIONS, "failed")
            return self._error_response(
                code=502,
                error=ErrorCode.ELASTICSEARCH_QUERY_FAILED,
                message=f"Failed to read rules from Elasticsearch: {exc}",
                correlation_id=correlation_id,
            )

        items = [
            r for r in result.get("items", [])
            if r.get("status") in (RuleStatus.ACTIVE, RuleStatus.FAILED, RuleStatus.PENDING)
        ]

        if not items:
            logger.info(
                "Replay: no replayable rules in ES — no-op",
                extra={
                    "correlation_id": correlation_id,
                    "stage": "replay_end",
                    "outcome": "success",
                },
            )
            _inc_replay_outcome(REPLAY_INVOCATIONS, "success")
            await self._refresh_rules_count_gauge()
            return {
                "status": ResponseStatus.SUCCESS,
                "message": "No replayable rules found",
                "correlation_id": correlation_id,
                "replayed": 0,
                "failed": 0,
                "total": 0,
                "details": [],
            }, 200

        async def _replay_one(rule_doc: Dict[str, Any]) -> Dict[str, Any]:
            rule_id = rule_doc.get("_id", "")
            entry: Dict[str, Any] = {
                "id": rule_id,
                "alert_type": rule_doc.get("alert_type", ""),
            }
            try:
                new_stream_id = await self._re_onboard_rule(
                    rule_id, rule_doc, correlation_id=correlation_id,
                )
                entry["result"] = "success"
                entry["rtvi_stream_id"] = new_stream_id
            except Exception as exc:
                entry["result"] = "error"
                entry["error"] = str(exc)
                _inc(REPLAY_RULE_FAILURES)
                logger.warning(
                    "Replay: rule %s failed — ES record marked FAILED",
                    rule_id,
                    extra={
                        "alert_rule_id": rule_id,
                        "alert_type": rule_doc.get("alert_type", ""),
                        "correlation_id": correlation_id,
                        "stage": "replay_rule",
                        "outcome": "failure",
                    },
                    exc_info=True,
                )
            return entry

        details = list(await asyncio.gather(*[_replay_one(r) for r in items]))
        replayed = sum(1 for d in details if d["result"] == "success")
        failed = sum(1 for d in details if d["result"] == "error")

        # Outcome label: success when every rule made it; failed when none
        # did; partial when at least one of each. Closed enum — see metric
        # definition in ``metrics/prometheus_metrics.py``.
        if failed == 0:
            outcome = "success"
        elif replayed == 0:
            outcome = "failed"
        else:
            outcome = "partial"
        _inc_replay_outcome(REPLAY_INVOCATIONS, outcome)
        await self._refresh_rules_count_gauge()

        logger.info(
            "Replay complete: replayed=%d failed=%d total=%d",
            replayed, failed, len(items),
            extra={
                "correlation_id": correlation_id,
                "stage": "replay_end",
                "outcome": outcome,
                "replayed": replayed,
                "failed": failed,
                "total": len(items),
            },
        )
        return {
            "status": ResponseStatus.SUCCESS,
            "message": "Replay completed",
            "correlation_id": correlation_id,
            "replayed": replayed,
            "failed": failed,
            "total": len(items),
            "details": details,
        }, 200

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_alert(
        self, config: AlertRuleConfig
    ) -> Tuple[Dict[str, Any], int]:
        """Create a real-time alert rule and return (response_dict, status_code).

        When a :class:`~.rule_store.RuleStore` is configured the rule is
        written to Elasticsearch **before** calling RTVI so the record
        exists even if the process crashes mid-flight.  RTVI failures
        roll back the ES record; ES write failures return 502 immediately.

        Returns 503 if a replay is currently in progress.
        """
        if self._replaying:
            return self._error_response(
                code=503,
                error="replay_in_progress",
                message="Cannot create rules while replay is in progress",
            )
        alert_rule_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        ctx = {"alert_rule_id": alert_rule_id, "alert_type": config.alert_type}

        model = config.model or self._default_model
        if not model:
            logger.error(
                "No VLM model resolved — request.model and rtvi_vlm.default_model both empty",
                extra={**ctx, "stage": "post", "outcome": "validation_failed"},
            )
            _inc_stage_failure(REALTIME_RULES_FAILED, "validation")
            return self._error_response(
                code=422,
                error=ErrorCode.VALIDATION_FAILED,
                message=(
                    "No VLM model configured: either set 'model' in the request "
                    "or 'rtvi_vlm.default_model' in the Alert Bridge config"
                ),
            )

        ctx["model"] = model
        ctx["live_stream_url"] = config.live_stream_url

        # ── Step 0: Persist to ES (persist-first) ─────────────────────
        if self._rule_store is not None:
            rule_doc = self._build_rule_doc(config, model, created_at)
            try:
                await asyncio.to_thread(
                    self._rule_store.create, alert_rule_id, rule_doc,
                )
                # ``REALTIME_RULES_PERSISTED`` is intentionally NOT
                # incremented here: a PENDING row in ES is not yet a
                # rule that operators or the replay endpoint can use,
                # and any failure in Steps 1–4 below will roll the
                # row back out.  The counter fires after Step 4 so
                # that ``…_persisted_total`` only ever counts rules
                # that reached ACTIVE status durably — see Finding 2
                # in the maintainer review.
                logger.info(
                    "Rule persisted to ES (status=pending)",
                    extra={**ctx, "stage": "post", "outcome": "persisted"},
                )
            except Exception as exc:
                logger.error(
                    "Failed to persist rule to Elasticsearch",
                    extra={
                        **ctx,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "stage": "post",
                        "outcome": "failure",
                    },
                )
                _inc_stage_failure(REALTIME_RULES_FAILED, "es_persist")
                return self._error_response(
                    code=502,
                    error=ErrorCode.ELASTICSEARCH_WRITE_FAILED,
                    message=f"Failed to persist rule to Elasticsearch: {exc}",
                )

        # ── Step 1: resolve or add the RTVI stream ────────────────────
        # ``_resolve_or_add_stream`` returns ``owns_stream=True`` when this
        # call issued ``/streams/add`` and ``False`` when it reused an
        # existing RTVI stream (matched by ``id == sensor_id``). The flag
        # propagates into the rule doc so ``stop_alert`` knows whether to
        # tear the underlying stream down on delete or only stop captions.
        try:
            rtvi_stream_id, owns_stream = await self._resolve_or_add_stream(
                config, ctx,
            )
        except httpx.HTTPError as exc:
            _inc_failure(RTVI_CALL_FAILURES, "start_stream")
            _inc_stage_failure(REALTIME_RULES_FAILED, "start_stream")
            logger.error(
                "RTVI start_stream failed",
                extra={
                    **ctx,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "stage": "post",
                    "outcome": "rtvi_start_stream_failed",
                },
            )
            await self._rollback_rule(alert_rule_id)
            return self._error_response(
                code=502,
                error=ErrorCode.RTVI_VLM_UNAVAILABLE,
                message=f"Failed to start realtime alert: {exc}",
            )
        except _StreamIdentityConflict as conflict:
            # Sensor id is already registered for a different live URL.
            # Reusing it would bind this rule to the wrong camera, so
            # roll back the PENDING ES row and tell the caller to pick
            # a different ``sensor_id`` (or reconcile the existing
            # registration). 409 matches the standard "your request
            # conflicts with the current resource state" semantics.
            await self._rollback_rule(alert_rule_id)
            return self._error_response(
                code=409,
                error=ErrorCode.RTVI_STREAM_CONFLICT,
                message=(
                    f"sensor_id '{conflict.sensor_id}' is already "
                    "registered with a different liveStreamUrl; refusing "
                    "to silently reuse the existing stream. Either "
                    "delete the existing rule using that id or pick a "
                    "different sensor_id."
                ),
            )
        except RuntimeError as exc:
            # ``_resolve_or_add_stream`` raises this when ``/streams/add``
            # returned 200 but no usable stream id — RTVI may have minted
            # an orphan we can't manage.
            _inc_stage_failure(REALTIME_RULES_FAILED, "missing_stream_id")
            logger.error(
                "RTVI start_stream returned no stream id — stream may be orphaned",
                extra={
                    **ctx,
                    "stage": "post",
                    "outcome": "missing_stream_id",
                    "error": str(exc),
                },
            )
            await self._rollback_rule(alert_rule_id)
            return self._error_response(
                code=502,
                error=ErrorCode.RTVI_INVALID_RESPONSE,
                message=(
                    "RTVI VLM accepted the stream but did not return a stream id; "
                    "rule cannot be managed and may be orphaned"
                ),
            )

        ctx["rtvi_stream_id"] = rtvi_stream_id
        ctx["owns_stream"] = owns_stream

        # Track this rule as an in-flight reader of ``rtvi_stream_id``
        # before we kick off ``generate_captions`` so a concurrent
        # sibling rolling back can see us via
        # :meth:`_count_other_rules_for_stream` even though our ES row
        # is still PENDING with no ``rtvi_stream_id`` set. The
        # corresponding unregister is in the ``finally`` block guarding
        # Steps 2–5: by the time we leave the block the rule is either
        # in ``self._rules`` / ES ACTIVE (so the regular ref-count
        # finds it) or fully rolled back (so it shouldn't be counted).
        self._register_pending_stream_ref(rtvi_stream_id, alert_rule_id)
        try:
            # ── Step 2: trigger caption generation ────────────────────
            captions_task = asyncio.create_task(
                self._client.generate_captions(
                    stream_id=rtvi_stream_id,
                    prompt=config.prompt,
                    model=model,
                    system_prompt=config.system_prompt,
                    chunk_duration=config.chunk_duration,
                    chunk_overlap_duration=config.chunk_overlap_duration,
                    alert_category=config.alert_type,
                    num_frames_per_second_or_fixed_frames_chunk=config.num_frames_per_second_or_fixed_frames_chunk,
                    use_fps_for_chunking=config.use_fps_for_chunking,
                    vlm_input_width=config.vlm_input_width,
                    vlm_input_height=config.vlm_input_height,
                    enable_reasoning=config.enable_reasoning,
                    api_type=config.api_type,
                    response_format=config.response_format,
                    stream_options=config.stream_options,
                    max_tokens=config.max_tokens,
                    temperature=config.temperature,
                    top_p=config.top_p,
                    top_k=config.top_k,
                    ignore_eos=config.ignore_eos,
                    seed=config.seed,
                    media_info=config.media_info,
                    enable_audio=config.enable_audio,
                    mm_processor_kwargs=config.mm_processor_kwargs,
                )
            )

            # ── Step 3: wait for ack / stream visibility ──────────────
            try:
                await self._wait_stream_ready(
                    captions_task, rtvi_stream_id, owns_stream, ctx,
                )
            except httpx.HTTPError as exc:
                # Ack-window failure: RTVI rejected ``generate_captions``
                # at the HTTP layer. Surface as RTVI_VLM_UNAVAILABLE —
                # the upstream is the problem, not the RTSP source.
                await self._rollback_rule(alert_rule_id)
                await self._maybe_stop_stream_on_rollback(
                    rtvi_stream_id, alert_rule_id, owns_stream, ctx,
                )
                return self._error_response(
                    code=502,
                    error=ErrorCode.RTVI_VLM_UNAVAILABLE,
                    message=f"Failed to start caption generation: {exc}",
                )
            except _StreamReadinessError as readiness_exc:
                # Readiness-phase failure: RTVI accepted the call but
                # the captions task crashed mid-stream (typical cause:
                # GStreamer could not open the RTSP source). Map to
                # ``RTVI_STREAM_NOT_READABLE`` so SDR can react to it.
                self._readiness_cleaned_streams.add(rtvi_stream_id)
                await self._mark_rule_failed(alert_rule_id)
                await self._maybe_stop_stream_on_rollback(
                    rtvi_stream_id, alert_rule_id, owns_stream, ctx,
                )
                return self._error_response(
                    code=502,
                    error=ErrorCode.RTVI_STREAM_NOT_READABLE,
                    message=(
                        f"Stream failed readiness check: {readiness_exc.cause}"
                    ),
                )

            # Register the captions task for late-failure cleanup.
            # Skipped when the task already finished inside the
            # ack/readiness window (no need for a done callback if
            # it's already done).
            if not captions_task.done():
                self._caption_tasks.add(captions_task)
                captions_task.add_done_callback(self._caption_tasks.discard)
                captions_task.add_done_callback(
                    lambda t, sid=rtvi_stream_id, rid=alert_rule_id, svc=self: svc._log_caption_task_result(t, sid, rid)
                )

            # ── Step 4: update ES with rtvi_stream_id ─────────────────
            if self._rule_store is not None:
                try:
                    await asyncio.to_thread(
                        self._rule_store.update,
                        alert_rule_id,
                        {
                            "rtvi_stream_id": rtvi_stream_id,
                            "status": RuleStatus.ACTIVE,
                            "owns_rtvi_stream": owns_stream,
                        },
                    )
                    # End-to-end durable success: the rule has both an
                    # ACTIVE row in ES and a live RTVI stream.  This is
                    # the earliest point at which the rule will survive
                    # a process restart unmodified, so it is the correct
                    # spot to increment ``REALTIME_RULES_PERSISTED`` —
                    # rolled-back PENDING rows from Steps 1–3 must not
                    # inflate the counter (Finding 2).
                    _inc(REALTIME_RULES_PERSISTED)
                    logger.info("ES rule updated with rtvi_stream_id (status=active)", extra=ctx)
                    await self._refresh_rules_count_gauge()
                except Exception as exc:
                    logger.error(
                        "Failed to update rule in ES after RTVI success — rolling back",
                        extra={
                            **ctx,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                            "stage": "post",
                            "outcome": "es_update_failed",
                        },
                    )
                    _inc_stage_failure(REALTIME_RULES_FAILED, "es_update")
                    # Drop the ES row first so the ref-count below
                    # excludes this rule, then tear the stream down
                    # only if no other rule was racing alongside us
                    # for the same stream id.
                    await self._rollback_rule(alert_rule_id)
                    await self._maybe_stop_stream_on_rollback(
                        rtvi_stream_id, alert_rule_id, owns_stream, ctx,
                    )
                    return self._error_response(
                        code=502,
                        error=ErrorCode.ELASTICSEARCH_WRITE_FAILED,
                        message=f"Failed to update rule in Elasticsearch: {exc}",
                    )

            # ── Readiness-failure guard ────────────────────────────────
            # The background readiness monitor may have fired and called
            # _cleanup_failed_rule while we were awaiting the ES update
            # above, potentially racing with (and losing to) our ACTIVE
            # write. Detect this and undo: mark ES FAILED and abort
            # before committing to _rules.
            if alert_rule_id in self._readiness_failed_ids:
                self._readiness_failed_ids.discard(alert_rule_id)
                logger.warning(
                    "Readiness failure detected after ES commit — marking rule failed and aborting",
                    extra={**ctx, "stage": "post", "outcome": "readiness_failed_after_es_commit"},
                )
                _inc_stage_failure(REALTIME_RULES_FAILED, "stream_readiness_post_commit")
                if self._rule_store is not None:
                    try:
                        await asyncio.to_thread(
                            self._rule_store.update,
                            alert_rule_id,
                            {"status": RuleStatus.FAILED, "rtvi_stream_id": None},
                        )
                    except Exception:
                        logger.exception(
                            "Failed to mark rule as failed in ES after post-commit readiness failure",
                            extra={"alert_rule_id": alert_rule_id},
                        )
                return self._error_response(
                    code=502,
                    error=ErrorCode.RTVI_STREAM_NOT_READABLE,
                    message="Stream failed readiness check during rule creation",
                )

            # ── Step 5: commit rule to in-memory registry ─────────────
            rule = {
                "id": alert_rule_id,
                "rtvi_stream_id": rtvi_stream_id,
                "owns_rtvi_stream": owns_stream,
                "sensor_id": config.sensor_id,
                "sensor_name": config.sensor_name,
                "live_stream_url": config.live_stream_url,
                "alert_type": config.alert_type,
                "prompt": config.prompt,
                "system_prompt": config.system_prompt,
                "model": model,
                "chunk_duration": config.chunk_duration,
                "chunk_overlap_duration": config.chunk_overlap_duration,
                "num_frames_per_second_or_fixed_frames_chunk": config.num_frames_per_second_or_fixed_frames_chunk,
                "use_fps_for_chunking": config.use_fps_for_chunking,
                "vlm_input_width": config.vlm_input_width,
                "vlm_input_height": config.vlm_input_height,
                "enable_reasoning": config.enable_reasoning,
                "status": RuleStatus.ACTIVE,
                "created_at": created_at,
            }
            # Include optional stream-identity / location fields only
            # when set — keeps the in-memory listing aligned with the
            # persistent ES doc (which also omits None via _build_rule_doc).
            for _field in STREAM_IDENTITY_OPTIONAL_FIELDS:
                _val = getattr(config, _field, None)
                if _val is not None:
                    rule[_field] = _val
            # Include optional extended fields only when set.
            for _field in EXTENDED_OPTIONAL_FIELDS:
                _val = getattr(config, _field, None)
                if _val is not None:
                    rule[_field] = _val
            with self._lock:
                self._rules[alert_rule_id] = rule

            if REALTIME_RULES_CREATED is not None:
                REALTIME_RULES_CREATED.inc()
            if REALTIME_RULES_ACTIVE is not None:
                REALTIME_RULES_ACTIVE.inc()

            logger.info(
                "Realtime alert rule created",
                extra={**ctx, "stage": "post", "outcome": "success"},
            )

            return {
                "status": ResponseStatus.SUCCESS,
                "id": alert_rule_id,
                "created_at": created_at,
                "message": "Realtime alert rule created",
            }, 201
        finally:
            self._unregister_pending_stream_ref(rtvi_stream_id, alert_rule_id)

    async def stop_alert(
        self,
        alert_rule_id: str,
    ) -> Tuple[Dict[str, Any], int]:
        """Delete an alert rule.

        Deletes the Elasticsearch record first, then calls RTVI VLM stop
        (tolerating VLM failures with a WARN log).

        Returns 503 if a replay is currently in progress.
        """
        if self._replaying:
            return self._error_response(
                code=503,
                error="replay_in_progress",
                message="Cannot delete rules while replay is in progress",
            )
        if self._rule_store is not None:
            return await self._stop_alert_persistent(alert_rule_id)

        return await self._stop_alert_memory(alert_rule_id)

    async def list_alerts(
        self,
        filters: Optional[Dict[str, Any]] = None,
        size: int = 100,
        from_: int = 0,
    ) -> Tuple[Dict[str, Any], int]:
        """List alert rules.

        When a :class:`~.rule_store.RuleStore` is configured, reads
        directly from Elasticsearch.  Otherwise falls back to the
        in-memory registry.
        """
        if self._rule_store is not None:
            return await self._list_alerts_persistent(filters, size, from_)
        return self._list_alerts_memory()

    async def get_alert(
        self, alert_rule_id: str
    ) -> Tuple[Dict[str, Any], int]:
        """Retrieve a single alert rule by ID.

        Reads from Elasticsearch when a :class:`~.rule_store.RuleStore`
        is configured; otherwise falls back to the in-memory registry.
        """
        if self._rule_store is not None:
            return await self._get_alert_persistent(alert_rule_id)
        return self._get_alert_memory(alert_rule_id)

    async def _stop_alert_persistent(
        self, alert_rule_id: str
    ) -> Tuple[Dict[str, Any], int]:
        """Delete durable record first, then best-effort RTVI teardown.

        Order rationale: deleting the ES record first guarantees the user
        can always clean up a stale rule — even during an RTVI outage.
        The previous order (RTVI first, ES second) returned 502 on RTVI
        failure and left the ES record in place, making user-DELETE
        impossible exactly when stale rules most need cleanup.  If the
        RTVI teardown fails after the ES record is gone, the orphaned
        RTVI stream is logged at WARNING for operator follow-up; RTVI
        will also time-out the stream on its own eventually.
        """
        ctx = {"alert_rule_id": alert_rule_id}

        # Read rule to get rtvi_stream_id before deleting
        try:
            rule = await asyncio.to_thread(self._rule_store.get, alert_rule_id)
        except Exception as exc:
            logger.error(
                "Failed to read rule from ES",
                extra={
                    **ctx,
                    "error": str(exc),
                    "stage": "delete",
                    "outcome": "es_read_failed",
                },
            )
            return self._error_response(
                code=502,
                error=ErrorCode.ELASTICSEARCH_QUERY_FAILED,
                message=f"Failed to read rule from Elasticsearch: {exc}",
            )

        if rule is None:
            return self._error_response(
                code=404,
                error=ErrorCode.NOT_FOUND,
                message=f"No active alert rule with id '{alert_rule_id}'",
            )

        rtvi_stream_id = rule.get("rtvi_stream_id")
        # ``owns_rtvi_stream`` is preserved as a diagnostic ("did this rule
        # originally add the stream?") but the actual stop_stream decision
        # below uses the live ref-count so a reuse rule that turns out to
        # be the *last* reader still cleans the stream up.
        owns_stream = rule.get("owns_rtvi_stream", True)
        ctx["rtvi_stream_id"] = rtvi_stream_id
        ctx["owns_stream"] = owns_stream

        # Step 1: delete the durable record so the rule is gone from the
        # user's perspective regardless of what happens with RTVI.
        try:
            deleted = await asyncio.to_thread(self._rule_store.delete, alert_rule_id)
        except Exception as exc:
            logger.error(
                "Failed to delete rule from ES",
                extra={
                    **ctx,
                    "error": str(exc),
                    "stage": "delete",
                    "outcome": "es_delete_failed",
                },
            )
            return self._error_response(
                code=502,
                error=ErrorCode.ELASTICSEARCH_WRITE_FAILED,
                message=f"Failed to delete rule from Elasticsearch: {exc}",
            )

        if not deleted:
            logger.info(
                "Rule already absent from ES (concurrent delete)",
                extra={**ctx, "stage": "delete", "outcome": "concurrent_delete"},
            )
            with self._lock:
                self._rules.pop(alert_rule_id, None)
            return self._error_response(
                code=404,
                error=ErrorCode.NOT_FOUND,
                message=f"No active alert rule with id '{alert_rule_id}'",
            )

        logger.info(
            "Deleted rule from ES",
            extra={**ctx, "stage": "delete", "outcome": "es_deleted"},
        )

        # Clean in-memory registry
        with self._lock:
            self._rules.pop(alert_rule_id, None)

        if REALTIME_RULES_DELETED is not None:
            REALTIME_RULES_DELETED.inc()
        if REALTIME_RULES_ACTIVE is not None:
            REALTIME_RULES_ACTIVE.dec()
        await self._refresh_rules_count_gauge()

        # Step 2: best-effort RTVI teardown driven by the live ref-count.
        # Count *other* rules that still reference the same stream id; if
        # this is the last reader, also call ``/streams/delete`` so the
        # RTVI stream is removed too. Otherwise leave it running for the
        # remaining sharers and only stop captions for this rule's
        # session.  Track outcome so the summary log line distinguishes
        # "full" delete (ES + RTVI both clean) from "partial" (ES gone,
        # RTVI orphaned).
        rtvi_outcome = "n/a"
        if rtvi_stream_id:
            other_count = await self._count_other_rules_for_stream(
                rtvi_stream_id, alert_rule_id,
            )
            ctx["other_active_rules"] = other_count
            rtvi_outcome = await self._safe_teardown_rtvi_with_outcome(
                rtvi_stream_id, ctx, stop_stream=(other_count == 0),
            )

        delete_outcome = "success" if rtvi_outcome in ("success", "n/a") else "partial"
        logger.info(
            "Realtime alert rule deleted",
            extra={
                **ctx,
                "stage": "delete",
                "outcome": delete_outcome,
                "rtvi_teardown": rtvi_outcome,
            },
        )
        return {
            "status": ResponseStatus.SUCCESS,
            "id": alert_rule_id,
            "message": "Realtime alert rule deleted",
        }, 200

    async def _list_alerts_persistent(
        self,
        filters: Optional[Dict[str, Any]],
        size: int,
        from_: int,
    ) -> Tuple[Dict[str, Any], int]:
        """List rules directly from Elasticsearch.

        Defaults to ``status=active`` when no explicit status filter is
        provided, matching the in-memory path's behaviour of only
        exposing live rules.
        """
        if filters is None:
            filters = {}
        if "status" not in filters:
            filters = {**filters, "status": RuleStatus.ACTIVE}

        try:
            result = await asyncio.to_thread(
                self._rule_store.list, filters=filters, size=size, from_=from_,
            )
        except Exception as exc:
            logger.error("Failed to list rules from ES: %s", exc, exc_info=True)
            return self._error_response(
                code=502,
                error=ErrorCode.ELASTICSEARCH_QUERY_FAILED,
                message=f"Failed to list rules from Elasticsearch: {exc}",
            )

        items = result.get("items", [])
        public_rules = [self._rule_to_public(r) for r in items]

        return {
            "status": ResponseStatus.SUCCESS,
            "rules": public_rules,
            "count": len(public_rules),
            "total": result.get("total", len(public_rules)),
        }, 200

    async def _get_alert_persistent(
        self, alert_rule_id: str
    ) -> Tuple[Dict[str, Any], int]:
        """Read a single rule from Elasticsearch."""
        try:
            rule = await asyncio.to_thread(self._rule_store.get, alert_rule_id)
        except Exception as exc:
            logger.error(
                "Failed to read rule from ES (id=%s): %s",
                alert_rule_id, exc, exc_info=True,
            )
            return self._error_response(
                code=502,
                error=ErrorCode.ELASTICSEARCH_QUERY_FAILED,
                message=f"Failed to read rule from Elasticsearch: {exc}",
            )

        if rule is None:
            return self._error_response(
                code=404,
                error=ErrorCode.NOT_FOUND,
                message=f"No alert rule with id '{alert_rule_id}'",
            )

        return {
            "status": ResponseStatus.SUCCESS,
            "rule": self._rule_to_public(rule),
        }, 200

    # ------------------------------------------------------------------
    # In-memory paths (legacy / tests / AlwaysOnService without ES)
    # ------------------------------------------------------------------

    async def _stop_alert_memory(
        self, alert_rule_id: str
    ) -> Tuple[Dict[str, Any], int]:
        """Original in-memory stop flow."""
        with self._lock:
            rule = self._rules.get(alert_rule_id)

        if rule is None:
            return self._error_response(
                code=404,
                error=ErrorCode.NOT_FOUND,
                message=f"No active alert rule with id '{alert_rule_id}'",
            )

        rtvi_stream_id = rule.get("rtvi_stream_id")
        owns_stream = rule.get("owns_rtvi_stream", True)
        ctx = {
            "alert_rule_id": alert_rule_id,
            "rtvi_stream_id": rtvi_stream_id,
            "owns_stream": owns_stream,
        }

        # Pop the rule first so the ref-count below excludes it.
        with self._lock:
            self._rules.pop(alert_rule_id, None)

        if rtvi_stream_id:
            other_count = await self._count_other_rules_for_stream(
                rtvi_stream_id, alert_rule_id,
            )
            ctx["other_active_rules"] = other_count
            await self._safe_teardown_rtvi(
                rtvi_stream_id, ctx, stop_stream=(other_count == 0),
            )

        if REALTIME_RULES_DELETED is not None:
            REALTIME_RULES_DELETED.inc()
        if REALTIME_RULES_ACTIVE is not None:
            REALTIME_RULES_ACTIVE.dec()

        logger.info(
            "Realtime alert rule deleted",
            extra={**ctx, "stage": "delete", "outcome": "success"},
        )
        return {
            "status": ResponseStatus.SUCCESS,
            "id": alert_rule_id,
            "message": "Realtime alert rule deleted",
        }, 200

    def _list_alerts_memory(self) -> Tuple[Dict[str, Any], int]:
        """Original in-memory list."""
        with self._lock:
            rules = list(self._rules.values())

        public_rules = [
            {k: v for k, v in r.items() if k not in _INTERNAL_FIELDS}
            for r in rules
        ]

        return {
            "status": ResponseStatus.SUCCESS,
            "rules": public_rules,
            "count": len(public_rules),
        }, 200

    def _get_alert_memory(
        self, alert_rule_id: str
    ) -> Tuple[Dict[str, Any], int]:
        """In-memory get by id."""
        with self._lock:
            rule = self._rules.get(alert_rule_id)

        if rule is None:
            return self._error_response(
                code=404,
                error=ErrorCode.NOT_FOUND,
                message=f"No alert rule with id '{alert_rule_id}'",
            )

        public = {k: v for k, v in rule.items() if k not in _INTERNAL_FIELDS}
        return {
            "status": ResponseStatus.SUCCESS,
            "rule": public,
        }, 200

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_start_stream_payload(config: AlertRuleConfig) -> Dict[str, Any]:
        """Build the payload for :meth:`RTVIVLMClient.start_stream`.

        Shared between :meth:`start_alert` and :meth:`_re_onboard_rule` so
        the two call sites can't drift — every field RTVI expects is
        set in exactly one place. ``sensor_id`` and ``sensor_name`` may
        be ``None`` (the caller omitted them, or the ES doc predates the
        field); the RTVI client forwards ``None`` as ``null`` and lets
        RTVI apply its own defaults. Optional identity / location fields
        are forwarded verbatim.
        """
        payload: Dict[str, Any] = {
            "id": config.sensor_id,
            "liveStreamUrl": config.live_stream_url,
            "sensor_name": config.sensor_name,
        }
        for _field in STREAM_IDENTITY_OPTIONAL_FIELDS:
            payload[_field] = getattr(config, _field, None)
        return payload

    @staticmethod
    def _build_rule_doc(
        config: AlertRuleConfig, model: str, created_at: str,
    ) -> Dict[str, Any]:
        """Build the ES document for a new rule (status=pending, no
        rtvi_stream_id yet).

        ``sensor_id`` and the stream-identity / location metadata fields
        (description, username, password, place_*) are persisted here so
        :meth:`_re_onboard_rule` can rebuild the full RTVI ``/streams/add``
        payload after a process restart. Without these fields a replay
        either ``TypeError``s on the ``AlertRuleConfig`` constructor
        (sensor_id is required) or ``KeyError``s on the RTVI client
        (``id`` is required by ``RTVIVLMClient.start_stream``), so all
        replayed rules would otherwise fail and operators would silently
        lose the values they set via POST.

        ``owns_rtvi_stream`` defaults to ``True`` here as the safe
        starting value: if the rule never reaches Step 4 (where the
        flag is overwritten with the resolved ownership) we want
        ``stop_alert`` to clean up any RTVI resources eagerly rather
        than leak them.
        """
        doc: Dict[str, Any] = {
            "live_stream_url": config.live_stream_url,
            "alert_type": config.alert_type,
            "sensor_id": config.sensor_id,
            "sensor_name": config.sensor_name,
            "prompt": config.prompt,
            "system_prompt": config.system_prompt,
            "model": model,
            "chunk_duration": config.chunk_duration,
            "chunk_overlap_duration": config.chunk_overlap_duration,
            "num_frames_per_second_or_fixed_frames_chunk": config.num_frames_per_second_or_fixed_frames_chunk,
            "use_fps_for_chunking": config.use_fps_for_chunking,
            "vlm_input_width": config.vlm_input_width,
            "vlm_input_height": config.vlm_input_height,
            "enable_reasoning": config.enable_reasoning,
            "status": RuleStatus.PENDING,
            "owns_rtvi_stream": True,
            "created_at": created_at,
        }
        # Include optional stream-identity / location fields only when set —
        # keeps the ES document compact for rules that don't use them and
        # preserves backward compatibility with old docs written before
        # these fields existed.
        for _field in STREAM_IDENTITY_OPTIONAL_FIELDS:
            _val = getattr(config, _field, None)
            if _val is not None:
                doc[_field] = _val
        # Include optional extended fields only when set so the ES document
        # stays compact and backward-compatible for rules that don't use them.
        for _field in EXTENDED_OPTIONAL_FIELDS:
            _val = getattr(config, _field, None)
            if _val is not None:
                doc[_field] = _val
        return doc

    @staticmethod
    def _rule_to_public(rule_doc: Dict[str, Any]) -> Dict[str, Any]:
        """Transform an ES rule document to the public API format.

        * ``_id`` → ``id``
        * Strip internal fields (``rtvi_stream_id``, ES metadata)
        """
        doc = {k: v for k, v in rule_doc.items() if k not in _INTERNAL_FIELDS}
        doc["id"] = rule_doc.get("_id", rule_doc.get("id", ""))
        return doc

    @staticmethod
    def _extract_stream_id(rtvi_resp: Dict[str, Any]) -> Optional[str]:
        """Pull the stream id out of an RTVI ``/streams/add`` response."""
        results = rtvi_resp.get("results")
        if isinstance(results, list) and results:
            stream_id = results[0].get("id")
            if stream_id:
                return stream_id
        return rtvi_resp.get("stream_id") or rtvi_resp.get("id")

    def _get_sensor_lock(self, sensor_id: str) -> asyncio.Lock:
        """Return the ``asyncio.Lock`` for ``sensor_id``, creating it lazily.

        Locks are kept for the lifetime of the service: the set of distinct
        ``sensor_id`` values is bounded by the number of cameras (in the
        thousands at most) so the memory overhead of never evicting them
        is negligible, and never evicting means we can't accidentally
        race a "free the lock" with a coroutine still waiting on it.

        The dict mutation is protected by ``self._lock`` (a regular
        ``threading.Lock``) because asyncio coroutines can be scheduled
        concurrently between ``await`` points and a bare ``setdefault``
        on a Python dict is *not* an atomic operation under the hood.
        """
        with self._lock:
            lock = self._sensor_locks.get(sensor_id)
            if lock is None:
                lock = asyncio.Lock()
                self._sensor_locks[sensor_id] = lock
            return lock

    async def _resolve_or_add_stream(
        self, config: AlertRuleConfig, ctx: Dict[str, Any],
    ) -> Tuple[str, bool]:
        """Resolve the RTVI stream id to use for this rule, adding one if needed.

        Returns ``(rtvi_stream_id, owns_stream)`` where ``owns_stream`` is
        ``True`` when this call was the one that issued ``/streams/add``
        (and is therefore responsible for tearing the stream down again on
        rule deletion), and ``False`` when an existing RTVI stream was
        reused.

        Behaviour:

        * No ``sensor_id`` on the config: skip the existence probe and
          ``/streams/add`` straight away. RTVI will mint a fresh id and
          this rule owns it. The probe is pointless because RTVI's
          ``streams/add`` does not deduplicate by URL anyway, so a check
          would never find a match.
        * ``sensor_id`` set: take the per-``sensor_id`` lock, call
          ``GET /streams/get-stream-info``, and look for an entry whose
          ``id`` matches ``sensor_id``. If found, validate that the
          existing registration's ``liveStreamUrl`` matches the
          requested ``live_stream_url`` before reusing — a mismatch
          means another caller (or a stale / externally-owned
          registration) already occupies this id pointing at a
          different camera, so silently reusing it would bind the new
          rule to the wrong source while persisting the requested URL
          on the rule document. Reject with
          :class:`_StreamIdentityConflict` instead. When the URL does
          match, reuse the stream id (``owns_stream=False``).
          Otherwise issue ``/streams/add`` while still holding the
          lock so a concurrent ``start_alert`` for the same
          ``sensor_id`` can't double-add.
        * If ``get-stream-info`` itself fails (network, RTVI restart
          mid-call): log a warning and fall back to ``/streams/add``
          unconditionally so a transient RTVI hiccup degrades gracefully
          instead of failing the whole rule creation.

        ``ctx`` is the per-request log context (already carries
        ``alert_rule_id``, ``alert_type``, ``model``, ``live_stream_url``).
        """
        sensor_id = config.sensor_id

        async def _add() -> Tuple[str, bool]:
            t0 = time.monotonic()
            try:
                rtvi_resp = await self._client.start_stream(
                    self._build_start_stream_payload(config)
                )
            finally:
                _observe(
                    RTVI_CALL_DURATION, "start_stream", time.monotonic() - t0,
                )
            stream_id = self._extract_stream_id(rtvi_resp)
            if not stream_id:
                raise RuntimeError("RTVI returned no stream id")
            return stream_id, True

        if sensor_id is None:
            stream_id, owns = await _add()
            logger.info(
                "RTVI stream added (no sensor_id, RTVI generated id)",
                extra={**ctx, "rtvi_stream_id": stream_id, "owns_stream": owns},
            )
            return stream_id, owns

        async with self._get_sensor_lock(sensor_id):
            t0 = time.monotonic()
            try:
                streams = await self._client.get_stream_info()
            except httpx.HTTPError as exc:
                _inc_failure(RTVI_CALL_FAILURES, "get_stream_info")
                logger.warning(
                    "get-stream-info probe failed; falling back to streams/add",
                    extra={
                        **ctx,
                        "sensor_id": sensor_id,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
                stream_id, owns = await _add()
                return stream_id, owns
            finally:
                _observe(
                    RTVI_CALL_DURATION, "get_stream_info", time.monotonic() - t0,
                )

            existing = next(
                (s for s in streams if s.get("id") == sensor_id),
                None,
            )
            if existing is not None:
                stream_id = existing.get("id") or sensor_id
                # Identity guard: when RTVI exposes the live URL on the
                # registration, refuse to silently bind to a stream
                # whose URL differs from what this caller asked for.
                # Anything else opens the door to a stale / externally
                # owned registration capturing the rule, and a later
                # last-reader delete tearing down a stream this service
                # never created. Older RTVI builds may omit
                # ``liveStreamUrl`` from the listing — when we don't
                # have a value to compare, log it but allow reuse so
                # the validation doesn't break working setups.
                requested_url = (config.live_stream_url or "").strip()
                existing_url = (existing.get("liveStreamUrl") or "").strip()
                if (
                    requested_url
                    and existing_url
                    and existing_url != requested_url
                ):
                    logger.warning(
                        "RTVI stream id collision: sensor_id is already "
                        "registered for a different liveStreamUrl",
                        extra={
                            **ctx,
                            "sensor_id": sensor_id,
                            "rtvi_stream_id": stream_id,
                            "requested_live_stream_url": requested_url,
                            "existing_live_stream_url": existing_url,
                            "stage": "post",
                            "outcome": "stream_identity_conflict",
                        },
                    )
                    _inc_stage_failure(
                        REALTIME_RULES_FAILED, "stream_identity_conflict",
                    )
                    raise _StreamIdentityConflict(
                        sensor_id=sensor_id,
                        requested_url=requested_url,
                        existing_url=existing_url,
                    )
                if not existing_url:
                    logger.debug(
                        "RTVI registration has no liveStreamUrl; reuse "
                        "identity check skipped",
                        extra={
                            **ctx,
                            "sensor_id": sensor_id,
                            "rtvi_stream_id": stream_id,
                        },
                    )
                logger.info(
                    "Reusing existing RTVI stream",
                    extra={
                        **ctx,
                        "sensor_id": sensor_id,
                        "rtvi_stream_id": stream_id,
                        "owns_stream": False,
                    },
                )
                return stream_id, False

            stream_id, owns = await _add()
            logger.info(
                "RTVI stream added (sensor_id was not registered)",
                extra={
                    **ctx,
                    "sensor_id": sensor_id,
                    "rtvi_stream_id": stream_id,
                    "owns_stream": owns,
                },
            )
            return stream_id, owns

    async def _wait_stream_ready(
        self,
        captions_task: asyncio.Task,
        rtvi_stream_id: str,
        owns_stream: bool,
        ctx: Dict[str, Any],
    ) -> None:
        """Wait for caption ack or stream visibility before returning 201.

        Two-phase wait that replaces the previous 12 s readiness window
        (``captions_ack_timeout`` + the deprecated
        ``stream_readiness_timeout``) with a much shorter probe-based
        check:

        1. **Ack window** (``captions_ack_timeout``, default 2 s). Wait
           on the ``generate_captions`` task under ``asyncio.shield`` so
           timing out here doesn't cancel the underlying HTTP request.
           A clean return = the captions task itself completed inside
           the window (full response body received) — the stream is
           good and we don't need to probe. An ``httpx.HTTPError`` here
           means RTVI rejected the request and is re-raised so the
           caller rolls back. ``TimeoutError`` is the normal path for
           streaming responses and falls through to phase 2.

        2. **Readiness window** (``stream_readiness_max_wait``, default
           2 s). For ``owns_stream=False`` (we just confirmed the stream
           exists in :meth:`_resolve_or_add_stream`) the window is
           skipped — there is nothing to verify and re-asking RTVI for
           something it just told us about is wasted latency. For new
           streams we monitor the captions task throughout the window:
           every ``stream_readiness_poll_interval`` seconds we re-check
           ``captions_task.done()`` and, until the stream first appears
           in the registry, ``GET /streams/get-stream-info``. We do
           *not* exit early on registry visibility — ``/streams/add``
           registers the stream synchronously while GStreamer can still
           fail to open the RTSP source milliseconds later, so leaving
           the window the moment the id shows up would let a fast
           post-registration failure slip past as a 201 and only get
           cleaned up asynchronously, contradicting the inline
           ``rtvi_stream_not_readable`` contract. Only a captions-task
           completion / failure or the ``max_wait`` deadline ends the
           window. A still-running task at the deadline is *not* a
           failure — late failures are caught by the done callback the
           caller attaches.

        Raises whatever exception ``captions_task`` produced when it
        fails so the caller can return 502 and roll back. Returns
        ``None`` on success / "survived without confirmation".
        """
        t0 = time.monotonic()
        try:
            try:
                await asyncio.wait_for(
                    asyncio.shield(captions_task),
                    timeout=self._captions_ack_timeout,
                )
                logger.info("Caption generation acknowledged (fast ack)", extra=ctx)
                return
            except asyncio.TimeoutError:
                pass
            except httpx.HTTPError as exc:
                _inc_failure(RTVI_CALL_FAILURES, "generate_captions")
                _inc_stage_failure(REALTIME_RULES_FAILED, "generate_captions")
                logger.error(
                    "generate_captions failed",
                    extra={
                        **ctx,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "stage": "post",
                        "outcome": "generate_captions_failed",
                    },
                )
                raise

            if not owns_stream:
                logger.info(
                    "Reused stream — readiness already verified, skipping poll",
                    extra={**ctx, "ack_timeout_s": self._captions_ack_timeout},
                )
                return

            deadline = time.monotonic() + self._stream_readiness_max_wait
            visible = False

            def _raise_task_failure(exc: BaseException) -> None:
                if isinstance(exc, httpx.HTTPError):
                    _inc_failure(RTVI_CALL_FAILURES, "generate_captions")
                    _inc_stage_failure(REALTIME_RULES_FAILED, "stream_readiness")
                else:
                    _inc_stage_failure(
                        REALTIME_RULES_FAILED, "stream_readiness_crash",
                    )
                logger.error(
                    "Caption task failed during readiness poll — rolling back",
                    extra={
                        **ctx,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "stage": "post",
                        "outcome": "stream_readiness_failed",
                    },
                )
                raise _StreamReadinessError(exc) from exc

            while True:
                if captions_task.done():
                    exc = captions_task.exception()
                    if exc is None:
                        logger.info(
                            "Caption task completed during readiness poll",
                            extra=ctx,
                        )
                        return
                    _raise_task_failure(exc)

                # Skip the probe once the stream has shown up in the
                # registry: re-asking RTVI for the same id every poll is
                # wasted load. The probe's only job is to confirm
                # ``/streams/add`` landed; the captions task is the
                # authority on whether the source is actually readable,
                # so we keep monitoring it for the full window even
                # after first sighting.
                if not visible:
                    try:
                        streams = await self._client.get_stream_info()
                        if any(s.get("id") == rtvi_stream_id for s in streams):
                            visible = True
                    except httpx.HTTPError as probe_exc:
                        logger.debug(
                            "get-stream-info probe failed during readiness; will retry",
                            extra={
                                **ctx,
                                "error": str(probe_exc),
                                "error_type": type(probe_exc).__name__,
                            },
                        )

                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(self._stream_readiness_poll_interval)

            # Final task check: catches the narrow window where the caption
            # task failed between the last in-loop check and exiting the
            # loop. Without this, a fast-failing task could slip through to
            # ES ACTIVE before the done callback's _readiness_failed_ids
            # guard fires.
            if captions_task.done() and captions_task.exception() is not None:
                _raise_task_failure(captions_task.exception())

            elapsed = time.monotonic() - t0
            logger.info(
                "Readiness window elapsed; captions task still running — "
                "proceeding (late failures handled by caption task callback)",
                extra={
                    **ctx,
                    "stream_visible": visible,
                    "max_wait_s": self._stream_readiness_max_wait,
                    "readiness_elapsed_s": elapsed,
                },
            )
        finally:
            # One histogram observation per call captures the total time
            # spent waiting — covers ack-only fast paths, reuse-skip
            # paths, the polling loop, and the error-raise paths
            # uniformly so dashboards always see a meaningful sample.
            _observe(
                RTVI_CALL_DURATION, "generate_captions", time.monotonic() - t0,
            )

    async def _maybe_stop_stream_on_rollback(
        self,
        rtvi_stream_id: str,
        alert_rule_id: str,
        owns_stream: bool,
        ctx: Dict[str, Any],
    ) -> None:
        """Tear down ``rtvi_stream_id`` on a rollback only when nobody else
        is using it.

        ``owns_stream=False`` (we reused a stream the original creator
        owns): never stop, ever — we're not the owner, the creator
        will clean up on its own delete.

        ``owns_stream=True`` but other rules race-reused the stream
        between our ``/streams/add`` and our failure: count them
        across both ACTIVE and PENDING (a concurrent in-flight create
        is still a valid reader) and skip ``stop_stream`` if
        non-zero. Otherwise we are the last reader and stop the stream.

        Counting includes PENDING *and* the in-memory pending stream
        refs maintained by :meth:`_register_pending_stream_ref`
        because the always-on fan-out path creates many rules in
        parallel; each holds the per-sensor lock briefly to add/reuse
        then continues asynchronously, and ``_build_rule_doc`` writes
        the PENDING ES row *before* ``rtvi_stream_id`` is known so
        the ES PENDING bucket alone would miss a sibling that has
        already reused the stream but hasn't yet hit the ACTIVE
        update. The in-memory mode (no rule store) similarly doesn't
        persist PENDING, so without the in-memory ref-count an
        in-flight sibling would be entirely invisible.
        """
        if not owns_stream:
            logger.info(
                "Rollback: not the stream owner — leaving RTVI stream alone",
                extra={**ctx, "rtvi_stream_id": rtvi_stream_id},
            )
            return

        other_count = await self._count_other_rules_for_stream(
            rtvi_stream_id,
            alert_rule_id,
            statuses=[RuleStatus.ACTIVE, RuleStatus.PENDING],
            include_pending_refs=True,
        )
        if other_count == 0:
            await self._safe_stop_stream(rtvi_stream_id)
            return

        logger.info(
            "Rollback: %d other rule(s) still reference this RTVI stream — "
            "skipping stop_stream",
            other_count,
            extra={
                **ctx,
                "rtvi_stream_id": rtvi_stream_id,
                "other_rules": other_count,
            },
        )

    def _register_pending_stream_ref(
        self, rtvi_stream_id: str, alert_rule_id: str,
    ) -> None:
        """Mark ``alert_rule_id`` as mid-create on ``rtvi_stream_id``.

        Must be called as soon as :meth:`_resolve_or_add_stream`
        returns a usable id (and before any subsequent ``await`` that
        could let a concurrent sibling roll back) so other coroutines
        querying :meth:`_count_other_rules_for_stream` with
        ``include_pending_refs=True`` can see this rule even while its
        ES PENDING row still has ``rtvi_stream_id=None``.
        """
        with self._lock:
            self._pending_stream_refs.setdefault(rtvi_stream_id, set()).add(
                alert_rule_id,
            )

    def _unregister_pending_stream_ref(
        self, rtvi_stream_id: str, alert_rule_id: str,
    ) -> None:
        """Drop the pending ref recorded by :meth:`_register_pending_stream_ref`.

        Idempotent — safe to call from a ``finally`` block regardless of
        whether the rule ultimately committed or rolled back.
        """
        with self._lock:
            refs = self._pending_stream_refs.get(rtvi_stream_id)
            if not refs:
                return
            refs.discard(alert_rule_id)
            if not refs:
                self._pending_stream_refs.pop(rtvi_stream_id, None)

    async def _count_other_rules_for_stream(
        self,
        rtvi_stream_id: str,
        exclude_rule_id: str,
        *,
        statuses: Optional[List[str]] = None,
        include_pending_refs: bool = False,
    ) -> int:
        """Return the number of *other* rules (excluding ``exclude_rule_id``)
        that currently reference ``rtvi_stream_id``.

        Used to drive the ref-count stop semantics: when a rule is
        deleted, the underlying RTVI stream is only torn down once
        this count drops to zero — i.e. the deleted rule was the last
        reader. This way two rules created via the reuse path can
        share a stream safely; whichever one is deleted last
        actually triggers ``/streams/delete``.

        Reads from Elasticsearch when persistence is enabled
        (so always-on fan-outs and survival across restarts are
        covered), otherwise scans the in-memory ``_rules`` registry.
        ``statuses`` defaults to ``[ACTIVE]`` for the deletion path;
        callers in rollback paths can widen this to include
        ``PENDING`` so concurrent in-flight creates aren't ignored.

        ``include_pending_refs`` (rollback / late-failure callers
        only) folds in the in-memory pending refs maintained by
        :meth:`_register_pending_stream_ref`. The ES PENDING bucket
        alone is *not* enough: ``_build_rule_doc`` writes the PENDING
        row before ``rtvi_stream_id`` is known and the in-memory mode
        doesn't persist PENDING at all, so a sibling that has just
        reused the stream but not yet reached the ACTIVE update would
        otherwise be invisible to ref-counting and the rolling-back
        owner would tear the shared stream down.

        Failures are degraded to ``0`` so a transient ES blip can't
        leave the user unable to delete a rule — the worst case is
        an unnecessary stream teardown, which the next reuser will
        re-create.
        """
        if statuses is None:
            statuses = [RuleStatus.ACTIVE]

        seen: Set[str] = set()

        if self._rule_store is not None:
            try:
                # The ES list helper accepts a single status filter;
                # query each status separately and de-dupe by alert
                # rule id so we count each rule once even if it
                # appears in multiple buckets (shouldn't, but
                # belt-and-braces against schema drift).
                for status in statuses:
                    try:
                        result = await asyncio.to_thread(
                            self._rule_store.list,
                            filters={
                                "rtvi_stream_id": rtvi_stream_id,
                                "status": status,
                            },
                            size=200,
                            from_=0,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to count rules for stream — assuming 0",
                            extra={
                                "rtvi_stream_id": rtvi_stream_id,
                                "exclude_rule_id": exclude_rule_id,
                                "status": status,
                                "error": str(exc),
                            },
                        )
                        continue
                    for item in result.get("items", []):
                        item_id = item.get("_id") or item.get("id")
                        if item_id and item_id != exclude_rule_id:
                            seen.add(item_id)
            except Exception:
                logger.exception(
                    "Unexpected error counting rules for stream — assuming 0",
                    extra={
                        "rtvi_stream_id": rtvi_stream_id,
                        "exclude_rule_id": exclude_rule_id,
                    },
                )
                seen.clear()
        else:
            with self._lock:
                for rule_id, rule in self._rules.items():
                    if (
                        rule_id != exclude_rule_id
                        and rule.get("rtvi_stream_id") == rtvi_stream_id
                    ):
                        seen.add(rule_id)

        if include_pending_refs:
            with self._lock:
                for rid in self._pending_stream_refs.get(
                    rtvi_stream_id, frozenset(),
                ):
                    if rid != exclude_rule_id:
                        seen.add(rid)

        return len(seen)

    async def _rollback_rule(self, alert_rule_id: str) -> None:
        """Best-effort rollback of an ES rule record."""
        if self._rule_store is None:
            return
        try:
            await asyncio.to_thread(self._rule_store.delete, alert_rule_id)
            logger.info(
                "Rolled back ES rule after failed creation",
                extra={"alert_rule_id": alert_rule_id},
            )
        except Exception:
            logger.error(
                "Failed to rollback ES rule %s — record may be orphaned",
                alert_rule_id,
                exc_info=True,
            )

    async def _mark_rule_failed(self, alert_rule_id: str) -> None:
        """Best-effort mark an ES rule record as FAILED and clear its stream id."""
        if self._rule_store is None:
            return
        try:
            await asyncio.to_thread(
                self._rule_store.update,
                alert_rule_id,
                {"status": RuleStatus.FAILED, "rtvi_stream_id": None},
            )
            logger.info(
                "Marked rule as failed in ES",
                extra={"alert_rule_id": alert_rule_id},
            )
        except Exception:
            logger.error(
                "Failed to mark rule %s as failed in ES — record may be stale",
                alert_rule_id,
                exc_info=True,
            )

    async def _safe_stop_stream(self, rtvi_stream_id: str) -> None:
        """Best-effort rollback. Logs but never raises."""
        t0 = time.monotonic()
        try:
            await self._client.stop_stream(rtvi_stream_id)
            _observe(RTVI_CALL_DURATION, "stop_stream", time.monotonic() - t0)
            logger.info(
                "Rolled back RTVI stream after failed creation",
                extra={"rtvi_stream_id": rtvi_stream_id},
            )
        except httpx.HTTPError as exc:
            _observe(RTVI_CALL_DURATION, "stop_stream", time.monotonic() - t0)
            _inc_failure(RTVI_CALL_FAILURES, "stop_stream")
            logger.error(
                "Rollback stop_stream failed — stream may be orphaned",
                extra={"rtvi_stream_id": rtvi_stream_id, "error": str(exc)},
            )

    async def _safe_teardown_rtvi(
        self,
        rtvi_stream_id: str,
        ctx: Dict[str, Any],
        stop_stream: bool = True,
    ) -> None:
        """Stop captions and (when ``stop_stream``) the stream concurrently.

        Best-effort — both calls tolerate failures so neither blocks
        nor aborts the other. ``stop_stream=False`` skips the
        ``/streams/delete`` call entirely so the underlying RTVI
        stream is left running for any other alert rules that are
        still reusing it. The caller is expected to compute that flag
        from the live refcount rather than just the deleted rule's
        ``owns_rtvi_stream`` attribute (see
        :meth:`_count_other_rules_for_stream`).
        """
        coros = [self._safe_stop_captions(rtvi_stream_id, ctx)]
        if stop_stream:
            coros.append(self._safe_stop_stream_with_ctx(rtvi_stream_id, ctx))
        else:
            logger.info(
                "Skipping stop_stream — other rules still use this RTVI stream",
                extra=ctx,
            )
        await asyncio.gather(*coros)

    async def _safe_teardown_rtvi_with_outcome(
        self,
        rtvi_stream_id: str,
        ctx: Dict[str, Any],
        stop_stream: bool = True,
    ) -> str:
        """Variant of :meth:`_safe_teardown_rtvi` that reports the outcome.

        Returns ``"success"`` when every call this rule was responsible
        for completed cleanly and ``"partial"`` when at least one
        raised an HTTPError. Used by :meth:`_stop_alert_persistent`
        so the summary log line can distinguish a clean delete from
        one that left an orphaned RTVI stream behind.

        ``stop_stream=False`` (other rules still use this stream)
        suppresses the ``/streams/delete`` call entirely — the rule
        being deleted is *not* the last reader of this stream. The
        outcome then reflects the captions teardown only.

        The work is duplicated from :meth:`_safe_stop_captions` and
        :meth:`_safe_stop_stream_with_ctx` rather than wrapping them
        because those helpers swallow ``httpx.HTTPError`` internally
        — there is no way to observe failure from the outside without
        re-implementing the inline try/except here.
        """
        async def _stop_captions() -> bool:
            t0 = time.monotonic()
            try:
                await self._client.stop_captions(rtvi_stream_id)
                _observe(RTVI_CALL_DURATION, "stop_captions", time.monotonic() - t0)
                logger.info("Stopped caption generation", extra=ctx)
                return True
            except httpx.HTTPError as exc:
                _observe(RTVI_CALL_DURATION, "stop_captions", time.monotonic() - t0)
                _inc_failure(RTVI_CALL_FAILURES, "stop_captions")
                logger.warning(
                    "stop_captions failed — continuing with stream delete",
                    extra={**ctx, "error": str(exc), "error_type": type(exc).__name__},
                )
                return False

        async def _stop_stream() -> bool:
            t0 = time.monotonic()
            try:
                await self._client.stop_stream(rtvi_stream_id)
                _observe(RTVI_CALL_DURATION, "stop_stream", time.monotonic() - t0)
                logger.info("Stopped RTVI stream", extra=ctx)
                return True
            except httpx.HTTPError as exc:
                _observe(RTVI_CALL_DURATION, "stop_stream", time.monotonic() - t0)
                _inc_failure(RTVI_CALL_FAILURES, "stop_stream")
                logger.warning(
                    "stop_stream failed — removing rule anyway to avoid wedged state",
                    extra={**ctx, "error": str(exc), "error_type": type(exc).__name__},
                )
                return False

        if stop_stream:
            captions_ok, stream_ok = await asyncio.gather(
                _stop_captions(), _stop_stream(),
            )
            return "success" if captions_ok and stream_ok else "partial"

        logger.info(
            "Skipping stop_stream — other rules still use this RTVI stream",
            extra=ctx,
        )
        captions_ok = await _stop_captions()
        return "success" if captions_ok else "partial"

    async def _refresh_rules_count_gauge(self) -> None:
        """Refresh ``REALTIME_RULES_COUNT`` from Elasticsearch.

        Cheap point-in-time read (``size=1`` so ES skips fetching hits)
        used after every successful create / delete / replay so the
        gauge reflects the durable count operators see in ES. ``.set()``
        is idempotent — last writer wins, which is the right semantic
        for a "current state" gauge under concurrent CRUD.

        Filters by ``status=ACTIVE`` so the gauge tracks only rules
        that are *usable*: PENDING rows from in-flight POSTs (which
        will either land at ACTIVE on success or be rolled back on
        failure) and crash-orphaned PENDINGs (visible for up to
        ``pending_ttl_seconds`` until the startup reaper clears them)
        are intentionally excluded.  An operator looking at
        ``alert_bridge_realtime_rules_count`` should see the same
        number they would get from ``GET /api/v1/realtime`` (which
        already filters by ACTIVE on the persistent path).

        Runs the synchronous ES client call inside ``asyncio.to_thread``
        so the event loop is never blocked on a network round-trip.
        No-op when persistence is disabled (no rule_store) or Prometheus
        is off; failures are swallowed so an ES blip never fails the
        request that triggered the refresh.
        """
        if self._rule_store is None or REALTIME_RULES_COUNT is None:
            return
        try:
            result = await asyncio.to_thread(
                self._rule_store.list,
                filters={"status": RuleStatus.ACTIVE},
                size=1,
                from_=0,
            )
            _set_gauge(REALTIME_RULES_COUNT, float(result.get("total", 0)))
        except Exception:
            logger.debug(
                "Failed to refresh realtime_rules_count gauge",
                exc_info=True,
            )

    async def _try_teardown_rtvi(
        self, rtvi_stream_id: str, ctx: Dict[str, Any],
    ) -> bool:
        """Tear down an RTVI stream and report whether it succeeded.

        Unlike :meth:`_safe_teardown_rtvi`, this variant returns ``True``
        only when the stream is confirmed stopped (or already gone) so
        callers can decide whether to persist the old stream id for a
        future cleanup pass.
        """
        try:
            await self._client.stop_captions(rtvi_stream_id)
        except Exception:
            pass

        t0 = time.monotonic()
        try:
            await self._client.stop_stream(rtvi_stream_id)
            _observe(RTVI_CALL_DURATION, "stop_stream", time.monotonic() - t0)
            logger.info("Stopped old RTVI stream", extra=ctx)
            return True
        except httpx.HTTPStatusError as exc:
            _observe(RTVI_CALL_DURATION, "stop_stream", time.monotonic() - t0)
            if exc.response.status_code == 404:
                logger.info("Old RTVI stream already gone (404)", extra=ctx)
                return True
            _inc_failure(RTVI_CALL_FAILURES, "stop_stream")
            logger.warning(
                "Old stream teardown failed",
                extra={**ctx, "error": str(exc), "error_type": type(exc).__name__},
            )
            return False
        except httpx.HTTPError as exc:
            _observe(RTVI_CALL_DURATION, "stop_stream", time.monotonic() - t0)
            _inc_failure(RTVI_CALL_FAILURES, "stop_stream")
            logger.warning(
                "Old stream teardown failed",
                extra={**ctx, "error": str(exc), "error_type": type(exc).__name__},
            )
            return False

    async def _safe_stop_captions(
        self, rtvi_stream_id: str, ctx: Dict[str, Any],
    ) -> None:
        """Best-effort caption stop.  Logs but never raises."""
        t0 = time.monotonic()
        try:
            await self._client.stop_captions(rtvi_stream_id)
            _observe(RTVI_CALL_DURATION, "stop_captions", time.monotonic() - t0)
            logger.info("Stopped caption generation", extra=ctx)
        except httpx.HTTPError as exc:
            _observe(RTVI_CALL_DURATION, "stop_captions", time.monotonic() - t0)
            _inc_failure(RTVI_CALL_FAILURES, "stop_captions")
            logger.warning(
                "stop_captions failed — continuing with stream delete",
                extra={**ctx, "error": str(exc), "error_type": type(exc).__name__},
            )

    async def _safe_stop_stream_with_ctx(
        self, rtvi_stream_id: str, ctx: Dict[str, Any],
    ) -> None:
        """Best-effort stream stop with context dict for logging."""
        t0 = time.monotonic()
        try:
            await self._client.stop_stream(rtvi_stream_id)
            _observe(RTVI_CALL_DURATION, "stop_stream", time.monotonic() - t0)
            logger.info("Stopped RTVI stream", extra=ctx)
        except httpx.HTTPError as exc:
            _observe(RTVI_CALL_DURATION, "stop_stream", time.monotonic() - t0)
            _inc_failure(RTVI_CALL_FAILURES, "stop_stream")
            logger.warning(
                "stop_stream failed — removing rule anyway to avoid wedged state",
                extra={**ctx, "error": str(exc), "error_type": type(exc).__name__},
            )

    async def _cleanup_failed_rule(
        self, rtvi_stream_id: str, alert_rule_id: Optional[str] = None
    ) -> None:
        """Clean up after a caption task fails post-ack-window.

        Stops the orphaned RTVI stream, removes the stale rule from the
        in-memory registry, and — when persistence is enabled — marks the rule
        as failed in Elasticsearch so it no longer appears as "active".

        ``alert_rule_id`` should be supplied by callers that know it so
        that cleanup can proceed even when the rule has not yet been
        committed to ``_rules``.  When omitted the method falls back to
        scanning ``_rules`` by ``rtvi_stream_id``.

        ``stop_stream`` is suppressed when *other* rules still reference
        the same RTVI stream id — counted via
        :meth:`_count_other_rules_for_stream` across both ACTIVE and
        PENDING statuses (and the in-memory pending stream refs
        registered before ``generate_captions``) so a concurrent
        in-flight create on the same ``sensor_id`` isn't accidentally
        killed by a different rule's late failure even when the
        sibling's ES PENDING row hasn't yet had ``rtvi_stream_id`` set.
        """
        if alert_rule_id is None:
            with self._lock:
                for rule_id, rule in self._rules.items():
                    if rule.get("rtvi_stream_id") == rtvi_stream_id:
                        alert_rule_id = rule_id
                        break

        if alert_rule_id:
            # Signal start_alert so it can abort before committing _rules /
            # writing ACTIVE to ES if it detects this flag after an await.
            self._readiness_failed_ids.add(alert_rule_id)

        other_count = 0
        if alert_rule_id is not None:
            other_count = await self._count_other_rules_for_stream(
                rtvi_stream_id,
                alert_rule_id,
                statuses=[RuleStatus.ACTIVE, RuleStatus.PENDING],
                include_pending_refs=True,
            )

        if other_count == 0:
            await self._safe_stop_stream(rtvi_stream_id)
        else:
            logger.info(
                "Skipping stop_stream during cleanup — %d other rule(s) still "
                "reference this RTVI stream",
                other_count,
                extra={
                    "alert_rule_id": alert_rule_id,
                    "rtvi_stream_id": rtvi_stream_id,
                    "other_rules": other_count,
                },
            )

        if alert_rule_id:
            with self._lock:
                removed = self._rules.pop(alert_rule_id, None)
            if removed:
                if REALTIME_RULES_ACTIVE is not None:
                    REALTIME_RULES_ACTIVE.dec()
                logger.info(
                    "Removed stale rule after caption task failure",
                    extra={"alert_rule_id": alert_rule_id, "rtvi_stream_id": rtvi_stream_id},
                )

            if self._rule_store is not None:
                try:
                    await asyncio.to_thread(
                        self._rule_store.update,
                        alert_rule_id,
                        {"status": RuleStatus.FAILED, "rtvi_stream_id": None},
                    )
                    logger.info(
                        "Marked rule as failed in Elasticsearch after caption task failure",
                        extra={"alert_rule_id": alert_rule_id, "rtvi_stream_id": rtvi_stream_id},
                    )
                except Exception:
                    logger.exception(
                        "Failed to update rule status in Elasticsearch after caption task failure",
                        extra={"alert_rule_id": alert_rule_id, "rtvi_stream_id": rtvi_stream_id},
                    )

            for cb in self._rule_removed_callbacks:
                asyncio.create_task(cb(alert_rule_id))
        else:
            logger.warning(
                "No rule found for failed stream — may have been deleted already",
                extra={"rtvi_stream_id": rtvi_stream_id},
            )

    def _log_caption_task_result(
        self,
        task: asyncio.Task,
        rtvi_stream_id: str,
        alert_rule_id: Optional[str] = None,
    ) -> None:
        """Log the outcome of a fire-and-forget caption task."""
        ctx = {"rtvi_stream_id": rtvi_stream_id}
        if alert_rule_id:
            ctx["alert_rule_id"] = alert_rule_id
        if task.cancelled():
            logger.info("Caption task cancelled", extra=ctx)
            return
        exc = task.exception()
        if exc is None:
            logger.info("Caption task finished cleanly", extra=ctx)
            return

        if rtvi_stream_id in self._readiness_cleaned_streams:
            self._readiness_cleaned_streams.discard(rtvi_stream_id)
            logger.debug(
                "Caption task failure already handled by readiness check — skipping duplicate cleanup",
                extra={**ctx, "error": str(exc), "error_type": type(exc).__name__},
            )
            return

        if isinstance(exc, httpx.HTTPError):
            _inc_failure(RTVI_CALL_FAILURES, "generate_captions")
            _inc_stage_failure(REALTIME_RULES_FAILED, "caption_task_http")
            logger.warning(
                "Caption task ended with HTTP error — scheduling cleanup",
                extra={**ctx, "error": str(exc), "error_type": type(exc).__name__},
            )
        else:
            _inc_stage_failure(REALTIME_RULES_FAILED, "caption_task_crash")
            logger.error(
                "Caption task crashed — scheduling cleanup",
                extra={**ctx, "error": str(exc), "error_type": type(exc).__name__},
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        # Populate _readiness_failed_ids synchronously here — done callbacks
        # fire synchronously in the event loop, so this is guaranteed to be
        # visible to start_alert's post-commit guard before start_alert can
        # resume from any subsequent await (e.g. the ES ACTIVE write).
        # Scheduling _cleanup_failed_rule as a task is not sufficient because
        # the task may not run until after start_alert's guard check passes.
        if alert_rule_id:
            self._readiness_failed_ids.add(alert_rule_id)
        asyncio.create_task(self._cleanup_failed_rule(rtvi_stream_id, alert_rule_id=alert_rule_id))

    @staticmethod
    def _error_response(
        code: int,
        error: str,
        message: str,
        correlation_id: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], int]:
        body: Dict[str, Any] = {
            "status": ResponseStatus.ERROR,
            "error": error,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        # Replay error paths echo the per-invocation id so operators can
        # grep logs by it even when the request short-circuited (501 /
        # 409) or failed mid-flight (502). Other call sites omit it.
        if correlation_id is not None:
            body["correlation_id"] = correlation_id
        return body, code

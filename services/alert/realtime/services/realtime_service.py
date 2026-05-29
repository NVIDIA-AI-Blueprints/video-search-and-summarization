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
    "rtvi_stream_id", "previous_rtvi_stream_id",
    "_id", "_index", "_seq_no", "_primary_term",
})


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
    2. Add the live stream to RTVI VLM (``/streams/add``).
       On failure → delete the ES record → return 502.
    3. Verify RTVI returned a stream id.
       On failure → delete the ES record → return 502.
    4. Trigger caption generation and await the ack window.  If the
       initial HTTP call fails → mark ES ``FAILED``, stop stream → 502.
    4b. If the ack window times out (streaming mode) and
       ``stream_readiness_timeout > 0``, hold the response open for
       up to ``stream_readiness_timeout`` additional seconds.  If the
       captions task fails during this window → mark ES ``FAILED``,
       stop stream → 502.  Total worst-case latency is
       ``captions_ack_timeout + stream_readiness_timeout`` (12 s default).
    5. Update the ES record with ``rtvi_stream_id`` and ``status=active``.
    6. Commit the rule to the in-memory registry and return 201.
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
        self._stream_readiness_timeout = rtvi_cfg.get(
            "stream_readiness_timeout", 10.0,
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
        # Async callbacks invoked when a rule is permanently removed by
        # readiness cleanup or a late caption-task failure.  Each is called
        # with the alert_rule_id string.
        self._rule_removed_callbacks: List[Callable[[str], Coroutine[Any, Any, None]]] = []
        self._replay_lock = asyncio.Lock()
        self._replaying: bool = False

        logger.info(
            "RealtimeAlertService initialized",
            extra={
                "rtvi_vlm_base_url": base_url,
                "default_model": self._default_model,
                "persistent": rule_store is not None,
                "captions_ack_timeout": self._captions_ack_timeout,
                "stream_readiness_timeout": self._stream_readiness_timeout,
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
        try:
            rtvi_resp = await self._client.start_stream(
                self._build_start_stream_payload(config)
            )
            rtvi_stream_id = self._extract_stream_id(rtvi_resp)
            if not rtvi_stream_id:
                raise RuntimeError("RTVI returned no stream id")
        except Exception:
            # start_stream failed or returned no usable stream id.
            # Mark the ES record FAILED so it doesn't remain ACTIVE/PENDING
            # and fire rule-removed callbacks to keep sidecars consistent.
            await self._mark_rule_failed(rule_id)
            for cb in self._rule_removed_callbacks:
                asyncio.create_task(cb(rule_id))
            raise

        replay_ctx = {
            "alert_rule_id": rule_id,
            "rtvi_stream_id": rtvi_stream_id,
            "correlation_id": correlation_id,
            "stage": "replay_rule",
        }

        t0 = time.monotonic()
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
            await asyncio.wait_for(
                asyncio.shield(captions_task), timeout=self._captions_ack_timeout,
            )
        except asyncio.TimeoutError:
            # Streaming mode — wait inline so the rule is only marked ACTIVE
            # after the stream survives the readiness window.
            if self._stream_readiness_timeout > 0:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(captions_task),
                        timeout=self._stream_readiness_timeout,
                    )
                    _observe(RTVI_CALL_DURATION, "generate_captions", time.monotonic() - t0)
                    logger.info(
                        "Caption generation completed during replay readiness window",
                        extra=replay_ctx,
                    )
                except asyncio.TimeoutError:
                    logger.info(
                        "Replay stream survived readiness window (%ss) without error — "
                        "not a positive readiness signal; late failures still possible",
                        self._captions_ack_timeout + self._stream_readiness_timeout,
                        extra=replay_ctx,
                    )
                except httpx.HTTPError as readiness_exc:
                    _observe(RTVI_CALL_DURATION, "generate_captions", time.monotonic() - t0)
                    _inc_failure(RTVI_CALL_FAILURES, "generate_captions")
                    _inc_stage_failure(REALTIME_RULES_FAILED, "stream_readiness")
                    logger.error(
                        "Stream failed readiness check during replay — rolling back",
                        extra={
                            **replay_ctx,
                            "error": str(readiness_exc),
                            "error_type": type(readiness_exc).__name__,
                            "outcome": "stream_readiness_failed",
                        },
                    )
                    self._readiness_cleaned_streams.add(rtvi_stream_id)
                    await self._safe_stop_stream(rtvi_stream_id)
                    await self._mark_rule_failed(rule_id)
                    for cb in self._rule_removed_callbacks:
                        asyncio.create_task(cb(rule_id))
                    raise
                except Exception as readiness_exc:
                    _observe(RTVI_CALL_DURATION, "generate_captions", time.monotonic() - t0)
                    _inc_stage_failure(REALTIME_RULES_FAILED, "stream_readiness_crash")
                    logger.error(
                        "Caption task crashed during replay readiness check — rolling back",
                        extra={
                            **replay_ctx,
                            "error": str(readiness_exc),
                            "error_type": type(readiness_exc).__name__,
                            "outcome": "stream_readiness_crash",
                        },
                        exc_info=True,
                    )
                    self._readiness_cleaned_streams.add(rtvi_stream_id)
                    await self._safe_stop_stream(rtvi_stream_id)
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
        except httpx.HTTPError as exc:
            _observe(RTVI_CALL_DURATION, "generate_captions", time.monotonic() - t0)
            _inc_failure(RTVI_CALL_FAILURES, "generate_captions")
            _inc_stage_failure(REALTIME_RULES_FAILED, "generate_captions")
            logger.error(
                "generate_captions failed during replay — rolling back stream",
                extra={
                    **replay_ctx,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "outcome": "generate_captions_failed",
                },
            )
            await self._safe_stop_stream(rtvi_stream_id)
            await self._mark_rule_failed(rule_id)
            for cb in self._rule_removed_callbacks:
                asyncio.create_task(cb(rule_id))
            raise

        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            await asyncio.to_thread(
                self._rule_store.update, rule_id, {
                    "rtvi_stream_id": rtvi_stream_id,
                    "last_replay_at": now_iso,
                    "status": RuleStatus.ACTIVE,
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
            await self._safe_stop_stream(rtvi_stream_id)
            await self._mark_rule_failed(rule_id)
            for cb in self._rule_removed_callbacks:
                asyncio.create_task(cb(rule_id))
            raise

        rule = {
            "id": rule_id,
            "rtvi_stream_id": rtvi_stream_id,
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
        # Carry the stream-identity / location metadata into the in-memory
        # registry too — GET /api/v1/realtime falls back to this dict when
        # the persistent listing path isn't in use, so the same fields
        # that survived ES must also reach the public response.
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
            # Per the maintainer review, the
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
            # Same rationale as the 501 path above: 409
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
                # that reached ACTIVE status durably.
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

        # ── Step 1: add stream to RTVI ────────────────────────────────
        stream_payload = self._build_start_stream_payload(config)

        t0 = time.monotonic()
        try:
            rtvi_resp = await self._client.start_stream(stream_payload)
            _observe(RTVI_CALL_DURATION, "start_stream", time.monotonic() - t0)
        except httpx.HTTPError as exc:
            _observe(RTVI_CALL_DURATION, "start_stream", time.monotonic() - t0)
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

        # ── Step 2: extract & validate stream id ──────────────────────
        rtvi_stream_id = self._extract_stream_id(rtvi_resp)
        if not rtvi_stream_id:
            _inc_stage_failure(REALTIME_RULES_FAILED, "missing_stream_id")
            logger.error(
                "RTVI start_stream returned no stream id — stream may be orphaned",
                extra={
                    **ctx,
                    "rtvi_response": rtvi_resp,
                    "stage": "post",
                    "outcome": "missing_stream_id",
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
        logger.info("RTVI stream started", extra=ctx)

        # ── Step 3: trigger caption generation ────────────────────────
        t0 = time.monotonic()
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
            await asyncio.wait_for(
                asyncio.shield(captions_task),
                timeout=self._captions_ack_timeout,
            )
            _observe(RTVI_CALL_DURATION, "generate_captions", time.monotonic() - t0)
            logger.info("Caption generation acknowledged (fast ack)", extra=ctx)
        except asyncio.TimeoutError:
            # Task still running — streaming mode.  Wait for the readiness
            # window inline so the caller only sees 201 once the stream is
            # verified.  Unreadable sources surface as 502 here instead of
            # being cleaned up after a false-positive 201.
            if self._stream_readiness_timeout > 0:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(captions_task),
                        timeout=self._stream_readiness_timeout,
                    )
                    _observe(RTVI_CALL_DURATION, "generate_captions", time.monotonic() - t0)
                    logger.info("Caption generation completed during readiness window", extra=ctx)
                except asyncio.TimeoutError:
                    logger.info(
                        "Stream survived readiness window (%ss) without error — "
                        "not a positive readiness signal; late failures still possible",
                        self._captions_ack_timeout + self._stream_readiness_timeout,
                        extra=ctx,
                    )
                except httpx.HTTPError as readiness_exc:
                    _observe(RTVI_CALL_DURATION, "generate_captions", time.monotonic() - t0)
                    _inc_failure(RTVI_CALL_FAILURES, "generate_captions")
                    _inc_stage_failure(REALTIME_RULES_FAILED, "stream_readiness")
                    logger.error(
                        "Stream failed during readiness check — rolling back",
                        extra={
                            **ctx,
                            "error": str(readiness_exc),
                            "error_type": type(readiness_exc).__name__,
                            "stage": "post",
                            "outcome": "stream_readiness_failed",
                            "readiness_window_s": self._stream_readiness_timeout,
                        },
                    )
                    self._readiness_cleaned_streams.add(rtvi_stream_id)
                    await self._mark_rule_failed(alert_rule_id)
                    await self._safe_stop_stream(rtvi_stream_id)
                    return self._error_response(
                        code=502,
                        error=ErrorCode.RTVI_STREAM_NOT_READABLE,
                        message=f"Stream failed readiness check: {readiness_exc}",
                    )
                except Exception as readiness_exc:
                    _observe(RTVI_CALL_DURATION, "generate_captions", time.monotonic() - t0)
                    _inc_stage_failure(REALTIME_RULES_FAILED, "stream_readiness_crash")
                    logger.error(
                        "Caption task crashed during readiness check — rolling back",
                        extra={
                            **ctx,
                            "error": str(readiness_exc),
                            "error_type": type(readiness_exc).__name__,
                            "stage": "post",
                            "outcome": "stream_readiness_crash",
                            "readiness_window_s": self._stream_readiness_timeout,
                        },
                        exc_info=True,
                    )
                    self._readiness_cleaned_streams.add(rtvi_stream_id)
                    await self._mark_rule_failed(alert_rule_id)
                    await self._safe_stop_stream(rtvi_stream_id)
                    return self._error_response(
                        code=502,
                        error=ErrorCode.RTVI_STREAM_NOT_READABLE,
                        message=f"Stream crashed during readiness check: {readiness_exc}",
                    )
            else:
                logger.info(
                    "Caption generation streaming (async) — readiness check disabled",
                    extra={**ctx, "ack_timeout_s": self._captions_ack_timeout},
                )

            if not captions_task.done():
                self._caption_tasks.add(captions_task)
                captions_task.add_done_callback(self._caption_tasks.discard)
                captions_task.add_done_callback(
                    lambda t, sid=rtvi_stream_id, rid=alert_rule_id, svc=self: svc._log_caption_task_result(t, sid, rid)
                )
            elif captions_task.exception() is not None:
                post_window_exc = captions_task.exception()
                _inc_stage_failure(REALTIME_RULES_FAILED, "stream_readiness_post_window")
                logger.error(
                    "Caption task failed immediately after readiness window — rolling back",
                    extra={
                        **ctx,
                        "error": str(post_window_exc),
                        "error_type": type(post_window_exc).__name__,
                        "stage": "post",
                        "outcome": "stream_readiness_post_window",
                    },
                )
                self._readiness_cleaned_streams.add(rtvi_stream_id)
                await self._mark_rule_failed(alert_rule_id)
                await self._safe_stop_stream(rtvi_stream_id)
                return self._error_response(
                    code=502,
                    error=ErrorCode.RTVI_STREAM_NOT_READABLE,
                    message=f"Stream failed immediately after readiness window: {post_window_exc}",
                )
        except httpx.HTTPError as exc:
            _observe(RTVI_CALL_DURATION, "generate_captions", time.monotonic() - t0)
            _inc_failure(RTVI_CALL_FAILURES, "generate_captions")
            _inc_stage_failure(REALTIME_RULES_FAILED, "generate_captions")
            logger.error(
                "generate_captions failed — rolling back stream",
                extra={
                    **ctx,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "stage": "post",
                    "outcome": "generate_captions_failed",
                },
            )
            await self._rollback_rule(alert_rule_id)
            await self._safe_stop_stream(rtvi_stream_id)
            return self._error_response(
                code=502,
                error=ErrorCode.RTVI_VLM_UNAVAILABLE,
                message=f"Failed to start caption generation: {exc}",
            )

        # ── Step 4: update ES with rtvi_stream_id ─────────────────────
        if self._rule_store is not None:
            try:
                await asyncio.to_thread(
                    self._rule_store.update,
                    alert_rule_id,
                    {"rtvi_stream_id": rtvi_stream_id, "status": RuleStatus.ACTIVE},
                )
                # End-to-end durable success: the rule has both an
                # ACTIVE row in ES and a live RTVI stream.  This is
                # the earliest point at which the rule will survive a
                # process restart unmodified, so it is the correct
                # spot to increment ``REALTIME_RULES_PERSISTED`` —
                # rolled-back PENDING rows from Steps 1–3 must not
                # inflate the counter.
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
                await self._safe_stop_stream(rtvi_stream_id)
                await self._rollback_rule(alert_rule_id)
                return self._error_response(
                    code=502,
                    error=ErrorCode.ELASTICSEARCH_WRITE_FAILED,
                    message=f"Failed to update rule in Elasticsearch: {exc}",
                )

        # ── Readiness-failure guard ────────────────────────────────────
        # The background readiness monitor may have fired and called
        # _cleanup_failed_rule while we were awaiting the ES update above,
        # potentially racing with (and losing to) our ACTIVE write.  Detect
        # this and undo: mark ES FAILED and abort before committing to _rules.
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

        # ── Step 5: commit rule to in-memory registry ─────────────────
        rule = {
            "id": alert_rule_id,
            "rtvi_stream_id": rtvi_stream_id,
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
        # Include optional stream-identity / location fields only when set —
        # keeps the in-memory listing aligned with the persistent ES doc
        # (which also omits None values via _build_rule_doc).
        for _field in STREAM_IDENTITY_OPTIONAL_FIELDS:
            _val = getattr(config, _field, None)
            if _val is not None:
                rule[_field] = _val
        # Include optional extended fields only when set
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
        ctx["rtvi_stream_id"] = rtvi_stream_id

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

        # Step 2: best-effort RTVI teardown (matches in-memory path behavior).
        # Track outcome so the summary log line distinguishes "full" delete
        # (ES + RTVI both clean) from "partial" (ES gone, RTVI orphaned).
        rtvi_outcome = "n/a"
        if rtvi_stream_id:
            rtvi_outcome = await self._safe_teardown_rtvi_with_outcome(
                rtvi_stream_id, ctx,
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
        ctx = {"alert_rule_id": alert_rule_id, "rtvi_stream_id": rtvi_stream_id}

        if rtvi_stream_id:
            await self._safe_teardown_rtvi(rtvi_stream_id, ctx)

        with self._lock:
            self._rules.pop(alert_rule_id, None)

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
        self, rtvi_stream_id: str, ctx: Dict[str, Any],
    ) -> None:
        """Stop captions and stream concurrently.  Best-effort — both
        calls tolerate failures so neither blocks nor aborts the other."""
        await asyncio.gather(
            self._safe_stop_captions(rtvi_stream_id, ctx),
            self._safe_stop_stream_with_ctx(rtvi_stream_id, ctx),
        )

    async def _safe_teardown_rtvi_with_outcome(
        self, rtvi_stream_id: str, ctx: Dict[str, Any],
    ) -> str:
        """Variant of :meth:`_safe_teardown_rtvi` that reports the outcome.

        Returns ``"success"`` when both stop_captions and stop_stream
        completed cleanly, ``"partial"`` when at least one raised an
        HTTPError. Used by :meth:`_stop_alert_persistent` so the
        summary log line can distinguish a clean delete from one that
        left an orphaned RTVI stream behind.

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

        captions_ok, stream_ok = await asyncio.gather(_stop_captions(), _stop_stream())
        return "success" if captions_ok and stream_ok else "partial"

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

        await self._safe_stop_stream(rtvi_stream_id)

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

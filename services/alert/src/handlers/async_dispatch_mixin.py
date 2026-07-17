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

from concurrent.futures import Future
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from metrics.recorder import inc_async_dispatch_fallback, set_dispatch_in_flight
from utils.logging_config import get_logger

logger = get_logger(__name__)

PIPELINE_MODE_SYNC = "sync"
PIPELINE_MODE_THREAD_BRIDGE = "thread_bridge"
PIPELINE_MODE_EVENT_LOOP = "event_loop"
PIPELINE_MODES = (
    PIPELINE_MODE_SYNC,
    PIPELINE_MODE_THREAD_BRIDGE,
    PIPELINE_MODE_EVENT_LOOP,
)


def resolve_pipeline_mode(raw_mode: Any, async_io_enabled: bool) -> str:
    """
    Resolve the effective pipeline mode.

    An explicit ``pipeline_mode`` must be valid — an invalid value raises so
    startup fails fast instead of silently running in a different mode. When
    unset, the mode derives from the legacy ``async_io.enabled`` flag so
    existing deployments keep their current behavior without config changes.
    """
    if raw_mode is not None:
        normalized = str(raw_mode).strip().lower()
        if normalized in PIPELINE_MODES:
            return normalized
        raise ValueError(
            f"Invalid pipeline_mode {raw_mode!r}: must be one of "
            f"{', '.join(PIPELINE_MODES)}"
        )
    return PIPELINE_MODE_THREAD_BRIDGE if async_io_enabled else PIPELINE_MODE_SYNC


def _effective_mode(instance) -> str:
    mode = getattr(instance, "pipeline_mode", None)
    if mode in PIPELINE_MODES:
        return mode
    return (
        PIPELINE_MODE_THREAD_BRIDGE
        if instance.async_io_enabled
        else PIPELINE_MODE_SYNC
    )


class AsyncDispatchMixin:
    def _effective_pipeline_mode(self) -> str:
        return _effective_mode(self)

    def _on_dispatched_message_done(
        self,
        future: Future,
        message_id: str,
        sensor_id: str,
        dispatch_slot_acquired: bool = False,
    ) -> None:
        with self._message_dispatch_lock:
            self._message_dispatch_futures.discard(future)
            in_flight = len(self._message_dispatch_futures)
        set_dispatch_in_flight(in_flight)

        if dispatch_slot_acquired and self._dispatch_backpressure_semaphore is not None:
            try:
                self._dispatch_backpressure_semaphore.release()
            except ValueError:
                logger.warning(
                    "Dispatch semaphore release skipped (already at max)",
                    extra={"message_id": message_id, "sensor_id": sensor_id},
                )

        try:
            if future.cancelled():
                raise RuntimeError("Dispatched future was cancelled")
            error = future.exception()
            if error is not None:
                raise error
            logger.debug(
                "Dispatched message completed",
                extra={"message_id": message_id, "sensor_id": sensor_id},
            )
        except Exception as exc:
            logger.error(
                "Dispatched message processing failed",
                extra={
                    "message_id": message_id,
                    "sensor_id": sensor_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )

    def _acquire_dispatch_slot(
        self,
        worker_id: int,
        message_id: str,
        sensor_id: str,
        dispatch_available: Callable[[], bool],
    ) -> bool:
        """
        Block on the backpressure semaphore until a slot frees or dispatch
        becomes unavailable. Returns whether a slot was acquired.
        """
        if self._dispatch_backpressure_semaphore is None:
            return False
        while True:
            if self._dispatch_backpressure_semaphore.acquire(timeout=1):
                return True
            with self._message_dispatch_lock:
                in_flight = len(self._message_dispatch_futures)
            logger.debug(
                "Async dispatch backlog full; waiting for slot",
                extra={
                    "worker_id": worker_id,
                    "message_id": message_id,
                    "sensor_id": sensor_id,
                    "in_flight": in_flight,
                    "max_in_flight": self.async_dispatch_max_in_flight,
                },
            )
            if not dispatch_available():
                return False

    def _track_dispatched_future(
        self,
        future: Future,
        worker_id: int,
        message_id: str,
        sensor_id: str,
        dispatch_slot_acquired: bool,
    ) -> None:
        with self._message_dispatch_lock:
            self._message_dispatch_futures.add(future)
            in_flight = len(self._message_dispatch_futures)
        set_dispatch_in_flight(in_flight)

        logger.debug(
            "Message queued for async dispatch",
            extra={
                "worker_id": worker_id,
                "message_id": message_id,
                "sensor_id": sensor_id,
                "in_flight": in_flight,
                "max_in_flight": self.async_dispatch_max_in_flight,
            },
        )

        future.add_done_callback(
            lambda done_future, msg_id=message_id, sid=sensor_id, slot_acquired=dispatch_slot_acquired: self._on_dispatched_message_done(
                done_future,
                msg_id,
                sid,
                slot_acquired,
            )
        )

    def _process_single_message_with_mode(
        self,
        worker_id: int,
        message: Dict[str, Any],
        kafka_consumed_at: Optional[str] = None,
        kafka_published_at: Optional[str] = None,
        worker_assigned_at: Optional[str] = None,
    ) -> None:
        """
        Dispatch message processing based on the effective pipeline mode.

        ``thread_bridge`` queues the blocking per-message flow onto a separate
        dispatch executor so batch workers can quickly return to the
        scheduling loop. ``event_loop`` submits a per-message coroutine to the
        persistent async runtime so in-flight concurrency is bounded by the
        backpressure semaphore rather than by thread count.

        ``worker_assigned_at`` is the ISO timestamp stamped by the batch
        scheduler the moment the sub-batch was dequeued from the worker
        queue (C24). Threading it down this call chain — instead of
        letting ``_process_single_message`` stamp its own timestamp —
        keeps ``WORKER_QUEUE_WAIT_DURATION`` semantics stable across
        sync and async modes.
        """
        mode = _effective_mode(self)
        if mode == PIPELINE_MODE_SYNC:
            self._process_single_message(
                worker_id,
                message,
                kafka_consumed_at,
                kafka_published_at,
                worker_assigned_at=worker_assigned_at,
            )
            return

        message_id = str(message.get("Id") or message.get("id") or "unknown")
        sensor_id = str(message.get("sensorId", "N/A"))

        if mode == PIPELINE_MODE_EVENT_LOOP:
            self._dispatch_message_to_event_loop(
                worker_id,
                message,
                message_id,
                sensor_id,
                kafka_consumed_at,
                kafka_published_at,
                worker_assigned_at,
            )
            return

        dispatch_slot_acquired = self._acquire_dispatch_slot(
            worker_id,
            message_id,
            sensor_id,
            lambda: self._message_dispatch_executor is not None,
        )

        dispatch_executor = self._message_dispatch_executor
        if dispatch_executor is None:
            if dispatch_slot_acquired and self._dispatch_backpressure_semaphore is not None:
                self._dispatch_backpressure_semaphore.release()
            logger.warning(
                "Async dispatch executor unavailable; falling back to inline message processing"
            )
            # C26: make the silent fallback visible on dashboards. Mirrors
            # the existing ``ASYNC_EXTERNAL_IO_FALLBACK_TOTAL`` pattern
            # used by the dedup-state / VST / Elastic mixins — operators who
            # already query ``rate(...)[5m]`` on that counter pick up the
            # dispatch fallbacks automatically (same metric, new
            # ``operation="dispatch_message"`` label value).
            inc_async_dispatch_fallback("executor_unavailable")
            self._process_single_message(
                worker_id,
                message,
                kafka_consumed_at,
                kafka_published_at,
                worker_assigned_at=worker_assigned_at,
            )
            return

        logger.debug(
            "Queueing message to async dispatch pipeline",
            extra={
                "worker_id": worker_id,
                "message_id": message_id,
                "sensor_id": sensor_id,
            },
        )
        try:
            future = dispatch_executor.submit(
                self._process_single_message,
                worker_id,
                message,
                kafka_consumed_at,
                kafka_published_at,
                worker_assigned_at,
            )
        except Exception as exc:
            if dispatch_slot_acquired and self._dispatch_backpressure_semaphore is not None:
                self._dispatch_backpressure_semaphore.release()
            logger.warning(
                "Async dispatch submit failed; falling back to inline processing",
                extra={
                    "worker_id": worker_id,
                    "message_id": message_id,
                    "sensor_id": sensor_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            # C26: reuses the ``submit_error`` reason already used by
            # ``_submit_sink_operation_with_mode`` in
            # ``AsyncExternalIOMixin`` so dashboards don't need a
            # special case — ``sum by (reason) (...)`` across both
            # operations gives the natural "how often does async
            # submission fall back" view.
            inc_async_dispatch_fallback("submit_error")
            self._process_single_message(
                worker_id,
                message,
                kafka_consumed_at,
                kafka_published_at,
                worker_assigned_at=worker_assigned_at,
            )
            return

        self._track_dispatched_future(
            future, worker_id, message_id, sensor_id, dispatch_slot_acquired
        )

    def _dispatch_message_to_event_loop(
        self,
        worker_id: int,
        message: Dict[str, Any],
        message_id: str,
        sensor_id: str,
        kafka_consumed_at: Optional[str],
        kafka_published_at: Optional[str],
        worker_assigned_at: Optional[str],
    ) -> None:
        dispatch_slot_acquired = self._acquire_dispatch_slot(
            worker_id,
            message_id,
            sensor_id,
            lambda: self.async_vlm_runtime is not None,
        )

        runtime = self.async_vlm_runtime
        if runtime is None:
            if dispatch_slot_acquired and self._dispatch_backpressure_semaphore is not None:
                self._dispatch_backpressure_semaphore.release()
            logger.warning(
                "Event-loop runtime unavailable; falling back to inline message processing"
            )
            inc_async_dispatch_fallback("runtime_unavailable")
            self._process_single_message(
                worker_id,
                message,
                kafka_consumed_at,
                kafka_published_at,
                worker_assigned_at=worker_assigned_at,
            )
            return

        task_dispatched_at = datetime.now(timezone.utc).isoformat()
        coroutine = self._process_single_message_async(
            worker_id,
            message,
            kafka_consumed_at,
            kafka_published_at,
            worker_assigned_at,
            task_dispatched_at,
        )
        try:
            future = runtime.submit_coroutine(coroutine)
        except Exception as exc:
            if dispatch_slot_acquired and self._dispatch_backpressure_semaphore is not None:
                self._dispatch_backpressure_semaphore.release()
            logger.warning(
                "Event-loop dispatch submit failed; falling back to inline processing",
                extra={
                    "worker_id": worker_id,
                    "message_id": message_id,
                    "sensor_id": sensor_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            inc_async_dispatch_fallback("submit_error")
            self._process_single_message(
                worker_id,
                message,
                kafka_consumed_at,
                kafka_published_at,
                worker_assigned_at=worker_assigned_at,
            )
            return

        self._track_dispatched_future(
            future, worker_id, message_id, sensor_id, dispatch_slot_acquired
        )

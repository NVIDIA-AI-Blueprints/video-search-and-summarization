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


def _fallback_to_inline(
    instance,
    reason: str,
    worker_id: int,
    message: Dict[str, Any],
    kafka_consumed_at: Optional[str],
    kafka_published_at: Optional[str],
    worker_assigned_at: Optional[str],
    dispatch_slot_acquired: bool,
) -> None:
    """Return the slot, record why, and process on the calling thread.

    Every dispatch failure ends here, so the semaphore is released in one
    place. The accounting used to be repeated at each of the five exits and
    had to be kept in step by hand. Module level rather than a method so a
    test driving the mixin with a mock ``self`` still exercises it.
    """
    if dispatch_slot_acquired and instance._dispatch_backpressure_semaphore is not None:
        instance._dispatch_backpressure_semaphore.release()
    inc_async_dispatch_fallback(reason)
    instance._process_single_message(
        worker_id,
        message,
        kafka_consumed_at,
        kafka_published_at,
        worker_assigned_at=worker_assigned_at,
    )


class AsyncDispatchMixin:
    @property
    def async_io_enabled(self) -> bool:
        """Whether thread_bridge machinery applies.

        Derived rather than stored: it was previously a second attribute
        assigned once from ``pipeline_mode``, which went stale the moment
        anything reassigned the mode.
        """
        return getattr(self, "pipeline_mode", None) == PIPELINE_MODE_THREAD_BRIDGE

    def _effective_pipeline_mode(self) -> str:
        return _effective_mode(self)

    def _on_dispatched_message_done(
        self,
        future: Future,
        message_id: str,
        sensor_id: str,
        dispatch_slot_acquired: bool = False,
        admission: Optional[Any] = None,
    ) -> None:
        if admission is not None:
            # First thing: a drain waiting on this partition must not be held
            # up by the reporting that follows.
            admission.release()

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
        admission: Optional[Any] = None,
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
            lambda done_future, msg_id=message_id, sid=sensor_id, slot_acquired=dispatch_slot_acquired, adm=admission: self._on_dispatched_message_done(
                done_future,
                msg_id,
                sid,
                slot_acquired,
                adm,
            )
        )

    def _process_single_message_with_mode(
        self,
        worker_id: int,
        message: Dict[str, Any],
        kafka_consumed_at: Optional[str] = None,
        kafka_published_at: Optional[str] = None,
        worker_assigned_at: Optional[str] = None,
        admission: Optional[Any] = None,
    ) -> None:
        """Dispatch one message according to the effective pipeline mode.

        The modes differ only in where the work runs: inline for ``sync``, on
        the dispatch executor for ``thread_bridge``, on the persistent event
        loop for ``event_loop``. The backpressure slot, the availability check,
        the fallback and the future bookkeeping are identical, so they are
        written once rather than once per mode.

        ``worker_assigned_at`` is stamped when the message was accepted for
        processing. Threading it down, instead of letting
        ``_process_single_message`` stamp its own, keeps
        ``WORKER_QUEUE_WAIT_DURATION`` comparable across modes.

        The admission is taken by the caller and released by whoever ends up
        owning the message: a dispatched message marks it transferred and its
        completion callback releases, while every other way out -- inline
        processing, a fallback, a submit that failed -- leaves it untransferred
        for the caller's ``finally``. A release added here as well would
        double-count, and one omitted there leaves a drain waiting out its
        whole timeout.
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
        on_event_loop = mode == PIPELINE_MODE_EVENT_LOOP

        if on_event_loop:
            missing_reason = "runtime_unavailable"
            missing_log = "Event-loop runtime unavailable; falling back to inline message processing"
            is_available = lambda: self.async_vlm_runtime is not None
        else:
            missing_reason = "executor_unavailable"
            missing_log = "Async dispatch executor unavailable; falling back to inline message processing"
            is_available = lambda: self._message_dispatch_executor is not None

        dispatch_slot_acquired = self._acquire_dispatch_slot(
            worker_id, message_id, sensor_id, is_available
        )

        # Read the target once: shutdown can tear it down between the
        # availability check and the submit.
        target = self.async_vlm_runtime if on_event_loop else self._message_dispatch_executor
        if target is None:
            logger.warning(missing_log)
            _fallback_to_inline(
                self, missing_reason, worker_id, message, kafka_consumed_at,
                kafka_published_at, worker_assigned_at, dispatch_slot_acquired,
            )
            return

        coroutine = None
        try:
            if on_event_loop:
                coroutine = self._process_single_message_async(
                    worker_id,
                    message,
                    kafka_consumed_at,
                    kafka_published_at,
                    worker_assigned_at,
                    datetime.now(timezone.utc).isoformat(),
                )
                future = target.submit_coroutine(coroutine)
                coroutine = None
            else:
                logger.debug(
                    "Queueing message to async dispatch pipeline",
                    extra={
                        "worker_id": worker_id,
                        "message_id": message_id,
                        "sensor_id": sensor_id,
                    },
                )
                future = target.submit(
                    self._process_single_message,
                    worker_id,
                    message,
                    kafka_consumed_at,
                    kafka_published_at,
                    worker_assigned_at,
                )
        except Exception as exc:
            # A coroutine that was built but never submitted would otherwise
            # surface as a "never awaited" RuntimeWarning far from the failure.
            if coroutine is not None:
                coroutine.close()
            logger.warning(
                "Dispatch submit failed; falling back to inline processing",
                extra={
                    "worker_id": worker_id,
                    "message_id": message_id,
                    "sensor_id": sensor_id,
                    "mode": mode,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            _fallback_to_inline(
                self, "submit_error", worker_id, message, kafka_consumed_at,
                kafka_published_at, worker_assigned_at, dispatch_slot_acquired,
            )
            return

        # From here the message outlives this call, so ownership of the
        # admission moves to the completion callback.
        self._track_dispatched_future(
            future, worker_id, message_id, sensor_id, dispatch_slot_acquired,
            admission=admission.transfer() if admission is not None else None,
        )

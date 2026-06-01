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

import copy
import time
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from typing import Any, Dict, Optional

from models.responses import EnrichmentResponse
from metrics import PROMETHEUS_ENABLED
from utils.logging_config import get_logger
from vst.exceptions import VSTTimeoutError

if PROMETHEUS_ENABLED:
    from metrics.prometheus_metrics import (
        ASYNC_EXTERNAL_IO_DURATION,
        ASYNC_EXTERNAL_IO_FALLBACK_TOTAL,
        ASYNC_SINK_IN_FLIGHT,
    )

logger = get_logger(__name__)


class AsyncExternalIOMixin:
    def _is_async_redis_mode_enabled(self) -> bool:
        return (
            self.async_redis_enabled
            and self.async_vlm_runtime is not None
            and self.redis_handler is not None
        )

    def _observe_async_external_io(
        self,
        operation_name: str,
        mode: str,
        result: str,
        duration_seconds: float,
    ) -> None:
        if not PROMETHEUS_ENABLED:
            return
        ASYNC_EXTERNAL_IO_DURATION.labels(
            operation=operation_name,
            mode=mode,
            result=result,
        ).observe(max(0.0, duration_seconds))

    def _count_async_external_fallback(self, operation_name: str, reason: str) -> None:
        if not PROMETHEUS_ENABLED:
            return
        ASYNC_EXTERNAL_IO_FALLBACK_TOTAL.labels(
            operation=operation_name,
            reason=reason,
        ).inc()

    def _run_redis_operation_with_mode(
        self,
        operation_name: str,
        operation_fn,
        *args,
        **kwargs,
    ):
        """
        Execute Redis operations via async runtime when enabled, with sync fallback.
        """
        sync_started_at = time.time()
        if not self._is_async_redis_mode_enabled():
            try:
                result = operation_fn(*args, **kwargs)
            except Exception:
                self._observe_async_external_io(
                    operation_name,
                    mode="sync",
                    result="error",
                    duration_seconds=time.time() - sync_started_at,
                )
                raise
            self._observe_async_external_io(
                operation_name,
                mode="sync",
                result="success",
                duration_seconds=time.time() - sync_started_at,
            )
            return result

        future: Optional[Future] = None
        async_started_at = time.time()
        try:
            future = self.async_vlm_runtime.submit_to_thread(
                operation_fn,
                *args,
                **kwargs,
            )
            result = future.result(timeout=self.async_external_timeout_seconds)
            self._observe_async_external_io(
                operation_name,
                mode="async",
                result="success",
                duration_seconds=time.time() - async_started_at,
            )
            return result
        except FutureTimeoutError as exc:
            if future is not None:
                future.cancel()
            self._observe_async_external_io(
                operation_name,
                mode="async",
                result="timeout",
                duration_seconds=time.time() - async_started_at,
            )
            self._count_async_external_fallback(operation_name, reason="timeout")
            logger.warning(
                "Async Redis operation timed out; falling back to sync call",
                extra={
                    "operation": operation_name,
                    "timeout_seconds": self.async_external_timeout_seconds,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
        except Exception as exc:
            self._observe_async_external_io(
                operation_name,
                mode="async",
                result="error",
                duration_seconds=time.time() - async_started_at,
            )
            self._count_async_external_fallback(operation_name, reason="error")
            logger.warning(
                "Async Redis operation failed; falling back to sync call",
                extra={
                    "operation": operation_name,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )

        fallback_started_at = time.time()
        try:
            result = operation_fn(*args, **kwargs)
        except Exception:
            self._observe_async_external_io(
                operation_name,
                mode="sync_fallback",
                result="error",
                duration_seconds=time.time() - fallback_started_at,
            )
            raise
        self._observe_async_external_io(
            operation_name,
            mode="sync_fallback",
            result="success",
            duration_seconds=time.time() - fallback_started_at,
        )
        return result

    def _is_async_elastic_sink_mode_enabled(self) -> bool:
        return (
            self.async_elastic_enabled
            and self.async_vlm_runtime is not None
            and self._vlm_sink_type == "elastic"
        )

    def _on_async_sink_operation_done(
        self,
        future: Future,
        operation_name: str,
        message_id: str,
        sensor_id: str,
        started_at: Optional[float] = None,
    ) -> None:
        with self._sink_async_lock:
            self._sink_async_futures.discard(future)
            in_flight = len(self._sink_async_futures)
        if PROMETHEUS_ENABLED:
            ASYNC_SINK_IN_FLIGHT.set(in_flight)
        duration = 0.0
        if started_at is not None:
            duration = max(0.0, time.time() - started_at)

        if future.cancelled():
            self._observe_async_external_io(
                operation_name,
                mode="async",
                result="cancelled",
                duration_seconds=duration,
            )
            logger.warning(
                "Async sink operation cancelled",
                extra={
                    "operation": operation_name,
                    "message_id": message_id,
                    "sensor_id": sensor_id,
                },
            )
            return

        error = future.exception()
        if error is not None:
            self._observe_async_external_io(
                operation_name,
                mode="async",
                result="error",
                duration_seconds=duration,
            )
            logger.error(
                "Async sink operation failed",
                extra={
                    "operation": operation_name,
                    "message_id": message_id,
                    "sensor_id": sensor_id,
                    "error": str(error),
                    "error_type": type(error).__name__,
                },
                exc_info=True,
            )
            return

        self._observe_async_external_io(
            operation_name,
            mode="async",
            result="success",
            duration_seconds=duration,
        )
        logger.debug(
            "Async sink operation completed",
            extra={
                "operation": operation_name,
                "message_id": message_id,
                "sensor_id": sensor_id,
            },
        )

    def _submit_sink_operation_with_mode(
        self,
        operation_name: str,
        operation_fn,
        message: Dict[str, Any],
        *args,
    ) -> Optional[Future]:
        message_id = str(message.get("Id") or message.get("id") or "unknown")
        sensor_id = str(message.get("sensorId", "N/A"))
        try:
            message_snapshot = copy.deepcopy(message)
        except Exception as exc:
            logger.warning(
                "Failed to deep-copy sink payload; using shallow copy",
                extra={
                    "operation": operation_name,
                    "message_id": message_id,
                    "sensor_id": sensor_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            message_snapshot = dict(message)

        sync_started_at = time.time()
        if not self._is_async_elastic_sink_mode_enabled():
            try:
                operation_fn(message_snapshot, *args)
            except Exception:
                self._observe_async_external_io(
                    operation_name,
                    mode="sync",
                    result="error",
                    duration_seconds=time.time() - sync_started_at,
                )
                raise
            self._observe_async_external_io(
                operation_name,
                mode="sync",
                result="success",
                duration_seconds=time.time() - sync_started_at,
            )
            return None

        submit_started_at = time.time()
        try:
            future = self.async_vlm_runtime.submit_to_thread(
                operation_fn,
                message_snapshot,
                *args,
            )
        except Exception as exc:
            self._observe_async_external_io(
                operation_name,
                mode="async_submit",
                result="error",
                duration_seconds=time.time() - submit_started_at,
            )
            self._count_async_external_fallback(operation_name, reason="submit_error")
            logger.warning(
                "Async sink submit failed; falling back to sync sink call",
                extra={
                    "operation": operation_name,
                    "message_id": message_id,
                    "sensor_id": sensor_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            fallback_started_at = time.time()
            try:
                operation_fn(message_snapshot, *args)
            except Exception:
                self._observe_async_external_io(
                    operation_name,
                    mode="sync_fallback",
                    result="error",
                    duration_seconds=time.time() - fallback_started_at,
                )
                raise
            self._observe_async_external_io(
                operation_name,
                mode="sync_fallback",
                result="success",
                duration_seconds=time.time() - fallback_started_at,
            )
            return None

        with self._sink_async_lock:
            self._sink_async_futures.add(future)
            in_flight = len(self._sink_async_futures)
        if PROMETHEUS_ENABLED:
            ASYNC_SINK_IN_FLIGHT.set(in_flight)
        if in_flight >= self.async_sink_warn_in_flight:
            logger.warning(
                "Async sink in-flight operations reached warning threshold",
                extra={
                    "operation": operation_name,
                    "in_flight": in_flight,
                    "warn_threshold": self.async_sink_warn_in_flight,
                },
            )

        future.add_done_callback(
            lambda done_future, op=operation_name, msg_id=message_id, sid=sensor_id, started=submit_started_at: self._on_async_sink_operation_done(
                done_future,
                op,
                msg_id,
                sid,
                started,
            )
        )
        return future

    def _publish_success_with_mode(
        self,
        message: Dict[str, Any],
        user_prompt: str,
        system_prompt: Optional[str],
        response_content: Any,
    ) -> Optional[Future]:
        return self._submit_sink_operation_with_mode(
            "publish_success",
            self.vlm_enhanced_event_sink.publish_success,
            message,
            user_prompt,
            system_prompt,
            response_content,
        )

    def _publish_error_with_mode(
        self,
        message: Dict[str, Any],
        user_prompt: str,
        system_prompt: Optional[str],
        error_payload: Dict[str, Any],
    ) -> Optional[Future]:
        return self._submit_sink_operation_with_mode(
            "publish_error",
            self.vlm_enhanced_event_sink.publish_error,
            message,
            user_prompt,
            system_prompt,
            error_payload,
        )

    def _update_enrichment_with_mode(
        self,
        message: Dict[str, Any],
        enrichment_result: EnrichmentResponse,
        publish_future: Optional[Future] = None,
    ) -> Optional[Future]:
        if not hasattr(self.vlm_enhanced_event_sink, "update_enrichment"):
            return None

        if publish_future is None:
            return self._submit_sink_operation_with_mode(
                "update_enrichment",
                self.vlm_enhanced_event_sink.update_enrichment,
                message,
                enrichment_result,
            )

        def _schedule_update(done_future: Future) -> None:
            if done_future.cancelled():
                logger.warning(
                    "Skipping enrichment update because sink publish was cancelled",
                    extra={"message_id": message.get("Id"), "sensor_id": message.get("sensorId")},
                )
                return
            publish_error = done_future.exception()
            if publish_error is not None:
                logger.warning(
                    "Skipping enrichment update because sink publish failed",
                    extra={
                        "message_id": message.get("Id"),
                        "sensor_id": message.get("sensorId"),
                        "error": str(publish_error),
                        "error_type": type(publish_error).__name__,
                    },
                )
                return
            self._submit_sink_operation_with_mode(
                "update_enrichment",
                self.vlm_enhanced_event_sink.update_enrichment,
                message,
                enrichment_result,
            )

        publish_future.add_done_callback(_schedule_update)
        return publish_future

    def _get_video_stream_url_with_mode(
        self,
        sensor_id: str,
        start_timestamp: str,
        end_timestamp: str,
        **kwargs,
    ):
        sync_started_at = time.time()
        if self.async_vst_enabled and self.async_vlm_runtime is not None:
            async_started_at = time.time()
            future = self.async_vlm_runtime.submit_to_thread(
                self._vst_handler.get_video_stream_url,
                sensor_id,
                start_timestamp,
                end_timestamp,
                **kwargs,
            )
            try:
                result = future.result(timeout=self.async_external_timeout_seconds)
                self._observe_async_external_io(
                    "get_video_stream_url",
                    mode="async",
                    result="success",
                    duration_seconds=time.time() - async_started_at,
                )
                return result
            except FutureTimeoutError as exc:
                future.cancel()
                self._observe_async_external_io(
                    "get_video_stream_url",
                    mode="async",
                    result="timeout",
                    duration_seconds=time.time() - async_started_at,
                )
                raise VSTTimeoutError(
                    "VST request timed out",
                    category="timeout",
                ) from exc
            except Exception:
                self._observe_async_external_io(
                    "get_video_stream_url",
                    mode="async",
                    result="error",
                    duration_seconds=time.time() - async_started_at,
                )
                raise
        try:
            result = self._vst_handler.get_video_stream_url(
                sensor_id,
                start_timestamp,
                end_timestamp,
                **kwargs,
            )
        except Exception:
            self._observe_async_external_io(
                "get_video_stream_url",
                mode="sync",
                result="error",
                duration_seconds=time.time() - sync_started_at,
            )
            raise
        self._observe_async_external_io(
            "get_video_stream_url",
            mode="sync",
            result="success",
            duration_seconds=time.time() - sync_started_at,
        )
        return result

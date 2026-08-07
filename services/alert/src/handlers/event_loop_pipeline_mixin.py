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

import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    UnprocessableEntityError,
)

from metrics.recorder import (
    dec_capacity_in_flight,
    inc_capacity_in_flight,
    inc_events_skipped_confirmed,
    observe_capacity_wait,
    observe_video_length,
    observe_vlm_duration,
    observe_vst_duration,
    record_event_complete,
)
from utils.logging_config import get_logger
from utils.time_utils import iso_delta_seconds
from vst.exceptions import VSTError

logger = get_logger(__name__)


class EventLoopPipelineMixin:
    """
    Coroutine-per-message pipeline for event_loop mode.

    One persistent event loop holds every in-flight message. All upstream I/O
    is awaited on the loop through async clients: the VLM via AsyncVLMClient,
    VST via httpx.AsyncClient, the Elastic sink and verdict checks via
    AsyncElasticsearch. Concurrency is bounded by semaphores instead of thread
    count: the dispatch backpressure semaphore globally, plus
    ``max_vlm_concurrent`` / ``max_vst_concurrent`` per service.
    """

    @asynccontextmanager
    async def _capacity_slot(self, semaphore, service: str, latency: Optional[Dict[str, Any]] = None):
        """Acquire a per-service concurrency slot, recording the wait separately
        from processing time and tracking per-service in-flight occupancy."""
        if semaphore is None:
            yield
            return
        wait_started = time.time()
        await semaphore.acquire()
        waited = time.time() - wait_started
        observe_capacity_wait(service, waited)
        if latency is not None:
            waits = latency.setdefault('capacityWait', {})
            waits[service] = round(waits.get(service, 0.0) + waited, 3)
        inc_capacity_in_flight(service)
        try:
            yield
        finally:
            dec_capacity_in_flight(service)
            semaphore.release()

    def _get_event_loop_http_client(self) -> httpx.AsyncClient:
        """Lazily create the shared async HTTP client (VST lookups and video
        URL validation). Created on the pipeline loop at first use."""
        client = getattr(self, "_event_loop_http_client", None)
        if client is None:
            client = httpx.AsyncClient(follow_redirects=True)
            self._event_loop_http_client = client
        return client

    async def _aclose_event_loop_clients(self) -> None:
        """Close async clients in dependency order: HTTP (VST) first, then the
        Elastic sink and verdict-check clients. The VLM client is closed by
        the runtime itself when the loop stops."""
        client = getattr(self, "_event_loop_http_client", None)
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                logger.exception("Failed closing event-loop HTTP client")
            self._event_loop_http_client = None

        sink = getattr(self, "vlm_enhanced_event_sink", None)
        if sink is not None and hasattr(sink, "aclose_async"):
            try:
                await sink.aclose_async()
            except Exception:
                logger.exception("Failed closing async Elastic sink client")

        handler = getattr(self, "redis_handler", None)
        es_client = getattr(handler, "_es_client", None) if handler is not None else None
        if es_client is not None and hasattr(es_client, "aclose_async"):
            try:
                await es_client.aclose_async()
            except Exception:
                logger.exception("Failed closing async verdict-check client")

    async def _analyze_video_url_async(
        self,
        video_url: str,
        user_prompt: str,
        system_prompt: Optional[str],
        num_frames: Optional[int] = 10,
        use_base64: bool = False,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        if use_base64:
            return await self.async_vlm_runtime.analyze_video_with_base64_async(
                video_url,
                user_prompt,
                system_prompt,
                num_frames=num_frames,
                config_overrides=config_overrides,
            )
        return await self.async_vlm_runtime.analyze_video_url_async(
            video_url,
            user_prompt,
            system_prompt,
            num_frames=num_frames,
            config_overrides=config_overrides,
        )

    async def _set_message_id_and_should_skip_async(
        self, message: Dict[str, Any], sensor_id: Any
    ) -> bool:
        """Async mirror of ``_set_message_id_and_should_skip`` — verdict-check
        goes through the async Elastic lookup."""
        fingerprint = self._compute_fingerprint(message)
        if not fingerprint:
            return False
        message["Id"] = fingerprint

        handler = getattr(self, "redis_handler", None)
        if handler is None:
            return False

        try:
            if hasattr(handler, "is_verdict_confirmed_async"):
                verdict_confirmed = await handler.is_verdict_confirmed_async(fingerprint)
            else:
                verdict_confirmed = await asyncio.to_thread(
                    handler.is_verdict_confirmed, fingerprint
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
            inc_events_skipped_confirmed(message)
            return True

        return False

    async def _prepare_message_context_async(
        self,
        message: Dict[str, Any],
        sensor_id: Any,
        latency: Dict[str, Any],
        worker_start_time: float,
    ) -> Optional[tuple]:
        """Async mirror of ``_prepare_message_context``."""
        # Reject messages missing fields the downstream VST stage dereferences
        # directly (``message['timestamp']`` / ``message['end']``). Mirrors the
        # sync guard in ``_prepare_message_context`` so producers that bypass the
        # HTTP JSON validation (protobuf endpoint, raw Kafka producer, replay
        # tooling) cannot trigger a ``KeyError`` deep in ``_resolve_video_url``.
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

        try:
            if await self._set_message_id_and_should_skip_async(message, sensor_id):
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

        user_prompt, system_prompt = await asyncio.to_thread(
            self.prompt_manager.get_prompts_for_message, message
        )

        if user_prompt is None and system_prompt is None:
            logger.warning("No prompt found [sensor=%s category=%s start=%s end=%s]",
                           sensor_id, message.get('category', 'N/A'), message.get('timestamp', 'N/A'), message.get('end', 'N/A'))
            record_event_complete(
                worker_start_time,
                message,
                latency,
                failure_reason="no_prompt",
            )
            return None

        return user_prompt, system_prompt

    async def _resolve_video_url_async(
        self,
        message: Dict[str, Any],
        sensor_id: Any,
        latency: Dict[str, Any],
    ) -> tuple:
        """Async mirror of ``_resolve_video_url`` — same window/retry logic and
        latency stamps, VST transport via httpx on the loop."""
        http_client = self._get_event_loop_http_client()
        objects_ids = message.get('objectIds', [])

        alert_type_anchor = None
        alert_type = message.get('category', '')
        if alert_type and self.prompt_manager and self.prompt_manager.alert_config_loader:
            alert_config = self.prompt_manager.alert_config_loader.get_config_for_alert_type(alert_type)
            if alert_config and alert_config.segment_anchor:
                alert_type_anchor = alert_config.segment_anchor
                logger.debug(f"Using per-alert-type segment_anchor='{alert_type_anchor}' for category='{alert_type}'")

        vst_error_captured = None
        vst_total_duration = 0.0
        video_url = None
        effective_start_time = None
        effective_end_time = None

        try:
            start = time.time()
            video_url, effective_start_time, effective_end_time = await self._vst_handler.get_video_stream_url_async(
                http_client,
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
                    video_url, effective_start_time, effective_end_time = await self._vst_handler.get_video_stream_url_async(
                        http_client,
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

    async def _validate_video_url_async(
        self, url: str, timeout: int = 10, max_retries: int = 8, retry_delay: float = 0.05
    ) -> bool:
        """Async mirror of ``validate_video_url`` (httpx streaming GET)."""
        client = self._get_event_loop_http_client()
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.debug(f"Retrying validation (attempt {attempt + 1}/{max_retries}) after {retry_delay}s delay")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.debug(f"Validating video URL: {url}")

                async with client.stream("GET", url, timeout=timeout) as response:
                    content_type = response.headers.get("content-type", "").lower()
                    content_length = response.headers.get("content-length", "0")
                    status_code = response.status_code

                    logger.debug(
                        f"URL validation - Status: {status_code}, Content-Type: {content_type}, Content-Length: {content_length} bytes"
                    )

                    if not (200 <= status_code < 300):
                        logger.warning(f"URL validation failed - HTTP Status: {status_code}")
                        if attempt < max_retries - 1:
                            continue
                        logger.error(f"URL validation failed after {max_retries} attempts - final status: {status_code}")
                        return False

                    try:
                        length = int(content_length)
                        if length == 0:
                            logger.warning("URL validation failed - Content-Length is 0, video file may not be ready")
                            if attempt < max_retries - 1:
                                continue
                            logger.error(f"URL validation failed after {max_retries} attempts - Content-Length still 0")
                            return False
                        if length < 1000:
                            logger.warning(f"URL has suspiciously small content-length: {length} bytes")
                    except ValueError:
                        logger.warning("Could not parse Content-Length header")

                    logger.info(f"URL validation successful on attempt {attempt + 1}")
                    return True

            except httpx.HTTPError as e:
                logger.warning(f"Request error validating URL (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    logger.error(f"URL validation failed after {max_retries} attempts due to request errors")
                    return False
            except Exception as e:
                logger.error(f"Unexpected error validating URL: {e}")
                return False

        return False

    async def _publish_outcome_and_complete_async(
        self,
        message: Dict[str, Any],
        user_prompt: str,
        system_prompt: Optional[str],
        response_content: Optional[str],
        vlm_failure_reason: Optional[str],
        worker_start_time: float,
        latency: Dict[str, Any],
    ) -> None:
        """Async mirror of ``_publish_outcome_and_complete`` — the Elastic sink
        publish is awaited on the loop; non-Elastic sinks fall back to the
        synchronous publish off-loop."""
        sink = self.vlm_enhanced_event_sink
        if not hasattr(sink, "publish_success_async"):
            await asyncio.to_thread(
                self._publish_outcome_and_complete,
                message,
                user_prompt,
                system_prompt,
                response_content,
                vlm_failure_reason,
                worker_start_time,
                latency,
            )
            return

        operation = "publish_success" if response_content is not None else "publish_error"
        started_at = time.time()
        try:
            if response_content is not None:
                await sink.publish_success_async(
                    message, user_prompt, system_prompt, response_content
                )
            else:
                await sink.publish_error_async(
                    message, user_prompt, system_prompt, {}
                )
        except Exception:
            self._observe_async_external_io(
                operation,
                mode="event_loop",
                result="error",
                duration_seconds=time.time() - started_at,
            )
            raise
        self._observe_async_external_io(
            operation,
            mode="event_loop",
            result="success",
            duration_seconds=time.time() - started_at,
        )

        self._complete_event_after_publish(
            None,
            worker_start_time,
            message,
            latency,
            failure_reason=vlm_failure_reason,
        )

    async def _publish_error_and_complete_async(
        self,
        message: Dict[str, Any],
        user_prompt: str,
        system_prompt: Optional[str],
        failure_reason: Optional[str],
        worker_start_time: float,
        latency: Dict[str, Any],
    ) -> None:
        await self._publish_outcome_and_complete_async(
            message,
            user_prompt,
            system_prompt,
            None,
            failure_reason,
            worker_start_time,
            latency,
        )

    async def _handle_vlm_exception_async(
        self,
        exc: Exception,
        message: Dict[str, Any],
        user_prompt: str,
        system_prompt: Optional[str],
        storage_video_url: Optional[str],
        worker_start_time: float,
        latency: Dict[str, Any],
    ) -> None:
        """Async mirror of ``_handle_vlm_exception`` — same classification and
        error document, awaited error publish."""
        failure_reason, log_label = self._apply_vlm_exception(
            exc, message, storage_video_url, latency
        )
        await self._publish_error_and_complete_async(
            message,
            user_prompt,
            system_prompt,
            failure_reason,
            worker_start_time,
            latency,
        )
        self._log_vlm_exception(log_label, message, exc)

    async def _process_enrichment_event_loop(
        self,
        message: Dict[str, Any],
        video_url: str,
        system_prompt: Optional[str],
        sensor_id: str,
        merged_vlm: Dict[str, Any],
    ) -> None:
        async def _analyze(url, prompt, sys_prompt):
            async with self._capacity_slot(self._vlm_capacity, 'vlm'):
                return await self.async_vlm_runtime.analyze_video_url_async(
                    url,
                    prompt,
                    sys_prompt,
                    config_overrides=merged_vlm,
                )

        enrichment_result = await self.enrichment_processor.process_async(
            message=message,
            video_url=video_url,
            system_prompt=system_prompt,
            sensor_id=sensor_id,
            analyze_video_async=_analyze,
            config_overrides=merged_vlm,
        )
        if enrichment_result:
            self.enrichment_processor.merge_into_message(message, enrichment_result)
            sink = self.vlm_enhanced_event_sink
            if hasattr(sink, "update_enrichment_async"):
                await sink.update_enrichment_async(message, enrichment_result)
            else:
                await asyncio.to_thread(
                    self._update_enrichment_with_mode,
                    message,
                    enrichment_result,
                    None,
                )

    async def _process_single_message_async(
        self,
        worker_id: int,
        message: Dict[str, Any],
        kafka_consumed_at: Optional[str] = None,
        kafka_published_at: Optional[str] = None,
        worker_assigned_at: Optional[str] = None,
        task_dispatched_at: Optional[str] = None,
    ) -> None:
        """Async mirror of ``_process_single_message`` built from the same
        stage helpers, so both modes share business logic and output contracts."""
        worker_start_time = time.time()
        task_started_at = datetime.now(timezone.utc).isoformat()
        if worker_assigned_at is None:
            worker_assigned_at = task_started_at
        sensor_id = message.get('sensorId')

        latency = {
            'timestamps': {
                'kafkaPublishedAt': kafka_published_at,
                'kafkaConsumedAt': kafka_consumed_at,
                'workerAssignedAt': worker_assigned_at,
                'taskDispatchedAt': task_dispatched_at,
                'taskStartedAt': task_started_at,
            },
        }

        prompts = await self._prepare_message_context_async(
            message, sensor_id, latency, worker_start_time
        )
        if prompts is None:
            return
        user_prompt, system_prompt = prompts

        video_url = None
        storage_video_url = None
        try:
            async with self._capacity_slot(self._vst_capacity, 'vst', latency):
                video_url, effective_start_time, effective_end_time, vst_error_captured = (
                    await self._resolve_video_url_async(message, sensor_id, latency)
                )

            if not video_url:
                await asyncio.to_thread(
                    self._handle_media_collection_failure,
                    message,
                    vst_error_captured,
                    worker_start_time,
                    latency,
                )
                return

            vlm_video_url, storage_video_url = self._transform_video_urls(video_url)

            async with self._capacity_slot(self._vst_capacity, 'vst', latency):
                video_url_valid = await self._validate_video_url_async(video_url)
            if not video_url_valid:
                await asyncio.to_thread(
                    self._handle_url_validation_failure,
                    message,
                    storage_video_url,
                    worker_start_time,
                    latency,
                )
                return

            category = message.get('category', '')
            merged_vlm = await asyncio.to_thread(self._get_merged_vlm_config, category)

            if merged_vlm.get('dynamic_frame_count', False):
                num_frames = self.set_max_frames(effective_start_time, effective_end_time)
            else:
                num_frames = merged_vlm.get('num_frames', 10)

            if os.getenv('LOG_VERBOSE_PROMPTS', 'false').lower() in ('1', 'true', 'yes'):
                logger.debug(f"User Prompt: {user_prompt}\nSystem Prompt: {system_prompt}")

            max_retries = merged_vlm.get('max_retries', 1)
            retry_delay = 0.5

            response_content = None
            verification_successful = False
            vlm_failure_reason = None  # set if VLM parse fails on last attempt

            for attempt in range(max_retries + 1):
                _attempt_start = time.time()
                _vlm_observed = False
                try:
                    logger.info("VLM request sent (attempt %d/%d, base64=%s) [sensor=%s category=%s start=%s end=%s]",
                                attempt + 1, max_retries + 1, self.vlm_media_source_using_base64,
                                sensor_id, message.get('category', 'N/A'), message.get('timestamp', 'N/A'), message.get('end', 'N/A'))
                    async with self._capacity_slot(self._vlm_capacity, 'vlm', latency):
                        start = time.time()
                        vlm_response = await self._analyze_video_url_async(
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
                    if not _vlm_observed:
                        observe_vlm_duration(
                            round(time.time() - _attempt_start, 3),
                            sensor_id,
                        )
                    if attempt < max_retries:
                        logger.warning("VLM API error (attempt %d/%d), retrying: %s", attempt + 1, max_retries + 1, e)
                        await asyncio.sleep(retry_delay)
                    else:
                        raise e

                except Exception as e:
                    if not _vlm_observed:
                        observe_vlm_duration(
                            round(time.time() - _attempt_start, 3),
                            sensor_id,
                        )
                    if attempt < max_retries:
                        logger.warning("VLM validation/processing error (attempt %d/%d), retrying: %s", attempt + 1, max_retries + 1, e)
                        await asyncio.sleep(retry_delay)
                    else:
                        vlm_failure_reason = self._apply_vlm_parse_failure(
                            message, e, response_content, storage_video_url, latency
                        )
                        response_content = None
                        break

            await self._publish_outcome_and_complete_async(
                message,
                user_prompt,
                system_prompt,
                response_content,
                vlm_failure_reason,
                worker_start_time,
                latency,
            )

            if verification_successful:
                await self._process_enrichment_event_loop(
                    message, vlm_video_url, system_prompt, sensor_id, merged_vlm
                )
        except Exception as e:
            await self._handle_vlm_exception_async(
                e,
                message,
                user_prompt,
                system_prompt,
                storage_video_url,
                worker_start_time,
                latency,
            )

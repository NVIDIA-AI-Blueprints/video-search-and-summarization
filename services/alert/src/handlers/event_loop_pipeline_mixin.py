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

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    UnprocessableEntityError,
)

from metrics.recorder import observe_capacity_wait, observe_vlm_duration
from utils.logging_config import get_logger

logger = get_logger(__name__)


class EventLoopPipelineMixin:
    """
    Coroutine-per-message pipeline for event_loop mode.

    One persistent event loop holds every in-flight message; the VLM call is
    awaited natively (no thread held for its latency) while short blocking
    stages (VST, sink, state checks) run on the runtime's bounded io thread
    pool. Concurrency is bounded by semaphores instead of thread count: the
    dispatch backpressure semaphore globally, ``max_vlm_concurrent`` /
    ``max_vst_concurrent`` per service.
    """

    @asynccontextmanager
    async def _capacity_slot(self, semaphore, service: str, latency: Optional[Dict[str, Any]] = None):
        """Acquire a per-service concurrency slot, recording the wait separately
        from processing time."""
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
        try:
            yield
        finally:
            semaphore.release()

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

        prompts = await asyncio.to_thread(
            self._prepare_message_context, message, sensor_id, latency, worker_start_time
        )
        if prompts is None:
            return
        user_prompt, system_prompt = prompts

        video_url = None
        storage_video_url = None
        try:
            async with self._capacity_slot(self._vst_capacity, 'vst', latency):
                video_url, effective_start_time, effective_end_time, vst_error_captured = (
                    await asyncio.to_thread(
                        self._resolve_video_url, message, sensor_id, latency
                    )
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
                video_url_valid = await asyncio.to_thread(self.validate_video_url, video_url)
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

            if verification_successful:
                await self._process_enrichment_event_loop(
                    message, vlm_video_url, system_prompt, sensor_id, merged_vlm
                )
        except Exception as e:
            await asyncio.to_thread(
                self._handle_vlm_exception,
                e,
                message,
                user_prompt,
                system_prompt,
                storage_video_url,
                worker_start_time,
                latency,
            )

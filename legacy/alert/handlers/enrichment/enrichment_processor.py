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

"""Enrichment processor for alert enhancement with VLM."""

import json
import logging
import os
import time
from typing import Dict, Any, Optional, Callable, Awaitable, Tuple

from openai import APIConnectionError, APITimeoutError, InternalServerError, UnprocessableEntityError

from models.responses import EnrichmentResponse
from vlm.vlm_client import VLMClient, AsyncVLMClient
from handlers.prompt_handler import PromptManager


class EnrichmentProcessor:
    """
    Handles enrichment prompt processing for alerts.
    
    Completely isolated from alert verification:
    - Never raises exceptions to caller
    - All errors captured in EnrichmentResponse
    - Feature flag controls execution
    """
    
    def __init__(
        self,
        vlm_client: VLMClient,
        prompt_manager: PromptManager,
        async_vlm_client: Optional[AsyncVLMClient] = None,
        enabled: bool = False,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the enrichment processor.
        
        Args:
            vlm_client: VLM client for making enrichment calls
            async_vlm_client: Async VLM client for non-blocking enrichment calls
            prompt_manager: Prompt manager for retrieving enrichment prompts
            enabled: Feature flag - when False, enrichment is skipped
            logger: Optional logger instance
        """
        self.vlm_client = vlm_client
        self.async_vlm_client = async_vlm_client
        self.prompt_manager = prompt_manager
        self.enabled = enabled
        self.logger = logger or logging.getLogger(self.__class__.__name__)
        
        self.logger.info(f"EnrichmentProcessor initialized ({'enabled' if self.enabled else 'disabled'})")

    def _prepare_enrichment_context(
        self,
        message: Dict[str, Any],
        sensor_id: str,
    ) -> Optional[Tuple[str, str, float]]:
        category = message.get('category', 'N/A')
        enrichment_prompt = self.prompt_manager.get_enrichment_prompt_for_message(message)
        if not enrichment_prompt:
            self.logger.debug(f"No enrichment prompt for category={category}")
            return None

        if os.getenv('LOG_VERBOSE_PROMPTS', 'false').lower() in ('1', 'true', 'yes'):
            self.logger.debug(f"Enrichment Prompt: {enrichment_prompt}")

        self.logger.info(f"Enrichment request sent [sensor={sensor_id} category={category}]")
        return category, enrichment_prompt, time.time()

    def _build_success_response(
        self,
        vlm_response: Any,
        sensor_id: str,
        category: str,
        start_time: float,
    ) -> EnrichmentResponse:
        duration = round(time.time() - start_time, 3)
        self.logger.info(
            f"Enrichment response received [sensor={sensor_id} category={category}] duration={duration:.3f}s"
        )

        response_content = vlm_response.content if vlm_response else None
        if os.getenv('LOG_VERBOSE_VLM_RESPONSE', 'false').lower() in ('1', 'true', 'yes'):
            self.logger.debug(f"Raw enrichment response: {response_content}")

        return EnrichmentResponse(
            reasoning=response_content,
            response_code=200,
            response_status="OK",
        )

    def _build_error_response(
        self,
        error: Exception,
        sensor_id: str,
        category: str,
    ) -> EnrichmentResponse:
        if isinstance(error, APITimeoutError):
            self.logger.warning(f"Enrichment timeout [sensor={sensor_id} category={category}]: {error}")
            return EnrichmentResponse(
                reasoning=None,
                response_code=504,
                response_status="VLM service timeout",
            )

        if isinstance(error, APIConnectionError):
            self.logger.warning(f"Enrichment connection error [sensor={sensor_id} category={category}]: {error}")
            return EnrichmentResponse(
                reasoning=None,
                response_code=503,
                response_status="Failed to connect to VLM service",
            )

        if isinstance(error, InternalServerError):
            self.logger.warning(f"Enrichment server error [sensor={sensor_id} category={category}]: {error}")
            return EnrichmentResponse(
                reasoning=None,
                response_code=500,
                response_status="VLM service internal error",
            )

        if isinstance(error, UnprocessableEntityError):
            self.logger.warning(f"Enrichment invalid request [sensor={sensor_id} category={category}]: {error}")
            return EnrichmentResponse(
                reasoning=None,
                response_code=422,
                response_status=f"VLM request invalid: {str(error)}",
            )

        self.logger.warning(f"Enrichment error [sensor={sensor_id} category={category}]: {error}")
        return EnrichmentResponse(
            reasoning=None,
            response_code=500,
            response_status=f"Enrichment error: {str(error)}",
        )
    
    def process(
        self,
        message: Dict[str, Any],
        video_url: str,
        system_prompt: Optional[str],
        sensor_id: str,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> Optional[EnrichmentResponse]:
        """
        Process enrichment for a successfully verified message.
        
        Args:
            message: Alert message dict
            video_url: Validated video URL (same as verification)
            system_prompt: System prompt (reused from verification)
            sensor_id: Sensor ID for logging
            config_overrides: Optional per-alert-type VLM param overrides
        
        Returns:
            EnrichmentResponse if processed, None if skipped
            
        Note:
            Never raises exceptions - all errors captured in response
        """
        if not self.enabled:
            return None
        
        context = self._prepare_enrichment_context(message, sensor_id)
        if context is None:
            return None
        category, enrichment_prompt, start_time = context

        try:
            analyze_kwargs: Dict[str, Any] = {}
            if config_overrides is not None:
                analyze_kwargs["config_overrides"] = config_overrides
            vlm_response = self.vlm_client.analyze_video_url(
                video_url,
                enrichment_prompt,
                system_prompt,
                **analyze_kwargs,
            )
            return self._build_success_response(
                vlm_response=vlm_response,
                sensor_id=sensor_id,
                category=category,
                start_time=start_time,
            )
        except Exception as e:
            return self._build_error_response(
                error=e,
                sensor_id=sensor_id,
                category=category,
            )

    async def process_async(
        self,
        message: Dict[str, Any],
        video_url: str,
        system_prompt: Optional[str],
        sensor_id: str,
        analyze_video_async: Optional[
            Callable[[str, str, Optional[str]], Awaitable[Any]]
        ] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> Optional[EnrichmentResponse]:
        """
        Async enrichment path with parity to process().
        """
        if not self.enabled:
            return None

        context = self._prepare_enrichment_context(message, sensor_id)
        if context is None:
            return None
        category, enrichment_prompt, start_time = context

        try:
            async_analyzer = analyze_video_async
            if async_analyzer is None and self.async_vlm_client is not None:
                analyze_kwargs: Dict[str, Any] = {}
                if config_overrides is not None:
                    analyze_kwargs["config_overrides"] = config_overrides
                vlm_response = await self.async_vlm_client.analyze_video_url(
                    video_url,
                    enrichment_prompt,
                    system_prompt,
                    **analyze_kwargs,
                )
                return self._build_success_response(
                    vlm_response=vlm_response,
                    sensor_id=sensor_id,
                    category=category,
                    start_time=start_time,
                )

            if async_analyzer is None:
                self.logger.warning(
                    "Async enrichment unavailable [sensor=%s category=%s]: no async VLM analyzer provided",
                    sensor_id,
                    category,
                )
                return EnrichmentResponse(
                    reasoning=None,
                    response_code=503,
                    response_status="Async VLM client unavailable",
                )

            vlm_response = await async_analyzer(
                video_url,
                enrichment_prompt,
                system_prompt,
            )
            return self._build_success_response(
                vlm_response=vlm_response,
                sensor_id=sensor_id,
                category=category,
                start_time=start_time,
            )
        except Exception as e:
            return self._build_error_response(
                error=e,
                sensor_id=sensor_id,
                category=category,
            )
    
    def merge_into_message(
        self,
        message: Dict[str, Any],
        enrichment_response: EnrichmentResponse,
    ) -> None:
        """
        Merge enrichment response into message['info']['enrichment'].
        
        Args:
            message: Alert message dict to update in-place
            enrichment_response: Response to merge
        """
        message.setdefault('info', {})['enrichment'] = json.dumps(
            enrichment_response.model_dump(), separators=(',', ':'),
        )

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

import time
from typing import Any, Dict, Optional

from openai.types.chat import ChatCompletionMessage

from models.responses import EnrichmentResponse
from utils.logging_config import get_logger

logger = get_logger(__name__)


class AsyncVLMModeMixin:
    def _analyze_video_url_with_mode(
        self,
        video_url: str,
        user_prompt: str,
        system_prompt: Optional[str],
        num_frames: Optional[int] = 10,
        use_base64: bool = False,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> ChatCompletionMessage:
        """
        Dispatch VLM video analysis through sync/async client based on guardrail flag.
        """
        if use_base64:
            if self.async_io_enabled and self.async_vlm_runtime is not None:
                return self.async_vlm_runtime.analyze_video_with_base64(
                    video_url,
                    user_prompt,
                    system_prompt,
                    num_frames=num_frames,
                    config_overrides=config_overrides,
                )
            return self.vlm_client.analyze_video_with_base64(
                video_url,
                user_prompt,
                system_prompt,
                num_frames=num_frames,
                config_overrides=config_overrides,
            )

        if self.async_io_enabled and self.async_vlm_runtime is not None:
            return self.async_vlm_runtime.analyze_video_url(
                video_url,
                user_prompt,
                system_prompt,
                num_frames=num_frames,
                config_overrides=config_overrides,
            )
        return self.vlm_client.analyze_video_url(
            video_url,
            user_prompt,
            system_prompt,
            num_frames=num_frames,
            config_overrides=config_overrides,
        )

    def _sleep_retry_with_mode(self, retry_delay: float) -> None:
        """
        Apply retry delay based on sync/async execution mode.
        """
        if self.async_io_enabled and self.async_vlm_runtime is not None:
            self.async_vlm_runtime.sleep(retry_delay)
            return
        time.sleep(retry_delay)

    def _process_enrichment_with_mode(
        self,
        message: Dict[str, Any],
        video_url: str,
        system_prompt: Optional[str],
        sensor_id: str,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> Optional[EnrichmentResponse]:
        """
        Dispatch enrichment through sync/async processor path based on guardrail flag.
        """
        if self.async_io_enabled and self.async_vlm_runtime is not None:
            return self.async_vlm_runtime.run_coroutine(
                self.enrichment_processor.process_async(
                    message=message,
                    video_url=video_url,
                    system_prompt=system_prompt,
                    sensor_id=sensor_id,
                    analyze_video_async=(
                        lambda async_video_url, async_user_prompt, async_system_prompt:
                        self.async_vlm_runtime.analyze_video_url_async(
                            async_video_url,
                            async_user_prompt,
                            async_system_prompt,
                            config_overrides=config_overrides,
                        )
                    ),
                    config_overrides=config_overrides,
                )
            )
        if self.async_io_enabled:
            logger.warning(
                "Async enrichment requested but async runtime is unavailable; falling back to sync path"
            )
        return self.enrichment_processor.process(
            message=message,
            video_url=video_url,
            system_prompt=system_prompt,
            sensor_id=sensor_id,
            config_overrides=config_overrides,
        )

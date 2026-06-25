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
On-demand verification service aligned with DirectMedia handler flow.

Accepts the same Incident payload that DirectMedia receives from Kafka.
Prompts are resolved via PromptManager.get_prompts_for_message() (Redis-backed).
VLM processing and sink publishing are delegated to DirectMediaHandler so the
full pipeline (VLM call, merge, publish to Kafka/ES) runs identically to the
Kafka-driven path.

The route calls ``prepare()`` synchronously (prompt resolution, validation),
then dispatches ``process_and_publish()`` as a background task and returns
HTTP 202 immediately.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from handlers.direct_media.direct_media_handler import DirectMediaHandler
from handlers.prompt_handler.prompt_manager import PromptManager
from mdx.anomaly.sink.vlm_enhanced_sink import build_vlm_enhanced_sink
from vlm.vlm_client import VLMClient

from ..core.dependencies import load_config, load_config_path


class AlertTypeNotFoundError(Exception):
    """Raised when category has no prompt configured."""


class OnDemandVerificationService:
    """Process on-demand verification requests using DirectMedia-aligned flow.

    ``prepare()`` validates the request and resolves prompts (fast, sync).
    ``process_and_publish()`` runs VLM + merge + publish via DirectMediaHandler
    (blocking, intended to run in a background task).
    """

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.config_file = load_config_path()
        self.config = load_config()

        self.vlm_client = VLMClient(self.config.get("vlm", {}))
        self.prompt_manager = PromptManager(self.config_file)

        # Pass the PromptManager's AlertConfigStore so the sink resolves
        # ``output_category`` from Redis on each publish (hot-reload of
        # PUT /verification/config edits) rather than the file-loaded
        # mapping cached at startup.
        self.vlm_enhanced_event_sink = build_vlm_enhanced_sink(
            self.config,
            alert_config_store=getattr(self.prompt_manager, "alert_config_store", None),
        )
        self.direct_media_handler = DirectMediaHandler(
            vlm_client=self.vlm_client,
            vlm_enhanced_event_sink=self.vlm_enhanced_event_sink,
            config=self.config,
        )

        self.max_media_count = (
            self.config.get("alert_agent", {})
            .get("media_download", {})
            .get("max_media_count", 5)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(
        self, request_data: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], str, str]:
        """Build the Incident message and resolve prompts.

        Fast and synchronous — safe to call before returning HTTP 202.

        Returns:
            ``(message, user_prompt, system_prompt)``

        Raises:
            AlertTypeNotFoundError: category has no configured prompt.
            ValueError: prompt manager failure or other validation issue.
        """
        message = dict(request_data)

        now = datetime.now(timezone.utc).isoformat()
        message.setdefault("id", f"ondemand-{uuid.uuid4()}")
        message.setdefault("sensorId", "ondemand")
        message.setdefault("timestamp", now)
        message.setdefault("end", now)

        info_block = message.get("info", {})
        media_urls = info_block.get("media_urls", [])

        if len(media_urls) > self.max_media_count:
            self.logger.warning(
                "media_urls count (%d) exceeds limit (%d), truncating",
                len(media_urls),
                self.max_media_count,
            )
            media_urls = media_urls[: self.max_media_count]
            message["info"]["media_urls"] = media_urls

        try:
            user_prompt, system_prompt = (
                self.prompt_manager.get_prompts_for_message(message)
            )
        except Exception as exc:
            raise ValueError(str(exc)) from exc

        if not user_prompt:
            raise AlertTypeNotFoundError(
                f"No prompt configuration found for category "
                f"'{message.get('category')}'"
            )

        return message, user_prompt, system_prompt

    def process_and_publish(
        self,
        message: Dict[str, Any],
        user_prompt: str,
        system_prompt: str,
    ) -> None:
        """Run VLM evaluation and publish results to Kafka/ES.

        Delegates entirely to :class:`DirectMediaHandler` so the processing
        pipeline is identical to the Kafka-driven path.  This method is
        blocking (synchronous) and is intended to run inside a background task.
        """
        info_block = message.get("info", {})
        self.direct_media_handler.evaluate(
            worker_id=0,
            message=message,
            info_block=info_block,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
        )

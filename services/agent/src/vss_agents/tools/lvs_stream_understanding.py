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

"""LVS stream summary/report tool."""

from collections.abc import AsyncGenerator
import json
import logging
import re
from typing import Any
from typing import Literal

import aiohttp
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

from vss_agents.tools.lvs_config_media import CAPTION_GENERATION_STARTED_MESSAGE
from vss_agents.tools.lvs_config_media import LVSMediaStatus
from vss_agents.tools.lvs_config_media import _coerce_lvs_response
from vss_agents.tools.lvs_media_state import configured_media

logger = logging.getLogger(__name__)


STREAM_SUMMARIZE_ENDPOINT = "/v1/stream_summarize"


class LVSStreamUnderstandingConfig(FunctionBaseConfig, name="lvs_stream_understanding"):
    """Configuration for the LVS stream summary/report tool."""

    lvs_backend_url: str = Field(..., description="The URL of the LVS backend service.")
    model: str = Field(default="gpt-4o", description="Model to use for LVS stream summarization.")
    conn_timeout_ms: int = Field(default=5000, description="Connection timeout in milliseconds.")
    read_timeout_ms: int = Field(default=600000, description="Read timeout in milliseconds.")

    model_config = ConfigDict(extra="forbid")


class LVSStreamUnderstandingInput(BaseModel):
    """Input for summarizing or reporting on an LVS-configured live stream."""

    stream_name: str = Field(..., description="The VST live stream/camera name.")
    start_time: float = Field(..., ge=0, description="Start time in seconds for the stream summary/report range.")
    end_time: float = Field(..., ge=0, description="End time in seconds for the stream summary/report range.")
    response_type: Literal["summary", "report"] = Field(
        default="summary",
        description="Whether to ask LVS for a summary or a report.",
    )

    @field_validator("stream_name")
    @classmethod
    def validate_stream_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("stream_name cannot be empty")
        return value.strip()

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def normalize_time(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"start", "beginning"}:
                return 0
            match = re.search(r"\d+(?:\.\d+)?", normalized)
            if match:
                return float(match.group())
        return value

    @field_validator("end_time")
    @classmethod
    def validate_time_range(cls, value: float, info: Any) -> float:
        start_time = info.data.get("start_time")
        if start_time is not None and value != 0 and value <= start_time:
            raise ValueError("end_time must be greater than start_time, or 0 for no upper bound")
        return value


class LVSStreamUnderstandingOutput(BaseModel):
    """Output from the LVS stream summary/report tool."""

    status: LVSMediaStatus = Field(..., description="Stream understanding status.")
    stream_name: str = Field(..., description="VST stream name.")
    stream_id: str | None = Field(default=None, description="VST stream ID.")
    configured: bool = Field(default=False, description="Whether the stream is configured for LVS.")
    message: str = Field(..., description="User-facing status message.")
    content: Any | None = Field(default=None, description="LVS summary/report content, if available.")
    lvs_backend_response: Any | None = Field(default=None, description="Raw LVS backend response, if any.")

    @property
    def summary(self) -> str | None:
        """Final-answer hint consumed by the top agent for terminal stream states."""
        if self.status == LVSMediaStatus.NOT_CONFIGURED:
            return None
        if self.status == LVSMediaStatus.SUCCESS:
            if isinstance(self.content, dict):
                formatted = self._format_lvs_content(self.content)
                if formatted:
                    return formatted
                for key in ("summary", "report"):
                    value = self.content.get(key)
                    if isinstance(value, str) and value.strip():
                        return value
            if isinstance(self.content, str) and self.content.strip():
                return self.content
            if self.content is not None:
                return json.dumps(self.content, indent=2, default=str)
        return self.message

    def _format_lvs_content(self, content: dict[str, Any]) -> str | None:
        """Render the LVS event-extraction response shape as plain text.

        Only the narrative ``video_summary`` is shown to the user; structured
        ``events`` remain available on ``self.content`` / ``self.lvs_backend_response``
        for downstream tools (e.g. report generation, search).

        Returns ``None`` if no narrative is present so the caller can fall back
        to other rendering strategies.

        Output shape::

            Stream Report: <stream_name>
            Summary: <video_summary>
        """
        video_summary_raw = content.get("video_summary")
        video_summary: str = video_summary_raw.strip() if isinstance(video_summary_raw, str) else ""
        if not video_summary:
            return None

        lines: list[str] = [
            f"Stream Report: {self.stream_name}",
            f"Summary: {video_summary}",
        ]
        return "\n".join(lines).rstrip() + "\n"


@register_function(config_type=LVSStreamUnderstandingConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def lvs_stream_understanding(config: LVSStreamUnderstandingConfig, _: Builder) -> AsyncGenerator[FunctionInfo]:
    """Summarize or report on a configured LVS live stream."""

    async def _lvs_stream_understanding(lvs_input: LVSStreamUnderstandingInput) -> LVSStreamUnderstandingOutput:
        """
        Summarize or generate a report for an LVS-captioned live stream over a time range.

        If the stream has not yet been set up for caption generation, this tool returns
        status=`not_configured` with a user-facing message asking whether to start
        generating captions. The agent MUST surface that message verbatim to the user
        and STOP — do NOT auto-call `lvs_config_media`. Caption generation is only
        triggered when the user explicitly replies with
        "start summarizing the stream <stream_name>".
        """
        configured = configured_media("stream", lvs_input.stream_name)
        if configured is None:
            return LVSStreamUnderstandingOutput(
                status=LVSMediaStatus.NOT_CONFIGURED,
                stream_name=lvs_input.stream_name,
                configured=False,
                message=(
                    f"There are no captions stored for stream '{lvs_input.stream_name}'. "
                    "Do you want me to start generating captions for the stream? "
                    f'If yes, please reply with: "start summarizing the stream {lvs_input.stream_name}".'
                ),
            )

        payload = {
            "id": configured.media_id,
            "model": config.model,
            "start_time": lvs_input.start_time,
            "end_time": lvs_input.end_time,
        }

        request_url = f"{config.lvs_backend_url.rstrip('/')}{STREAM_SUMMARIZE_ENDPOINT}"
        logger.info(
            "LVS %s request: stream=%r media_id=%s url=%s payload=%s",
            STREAM_SUMMARIZE_ENDPOINT,
            configured.media_name,
            configured.media_id,
            request_url,
            payload,
        )

        timeout = aiohttp.ClientTimeout(connect=config.conn_timeout_ms / 1000, total=config.read_timeout_ms / 1000)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(request_url, json=payload) as response,
            ):
                response_text = await response.text()
                logger.info(
                    "LVS %s response: stream=%r media_id=%s status=%d body=%s",
                    STREAM_SUMMARIZE_ENDPOINT,
                    configured.media_name,
                    configured.media_id,
                    response.status,
                    response_text,
                )
                if response.status not in (200, 201, 202):
                    return LVSStreamUnderstandingOutput(
                        status=LVSMediaStatus.FAILED,
                        stream_name=configured.media_name,
                        stream_id=configured.media_id,
                        configured=True,
                        message=f"LVS {STREAM_SUMMARIZE_ENDPOINT} failed with status {response.status}: {response_text}",
                    )

                if response.status == 202 or not response_text:
                    return LVSStreamUnderstandingOutput(
                        status=LVSMediaStatus.ACCEPTED,
                        stream_name=configured.media_name,
                        stream_id=configured.media_id,
                        configured=True,
                        message=CAPTION_GENERATION_STARTED_MESSAGE,
                    )

                try:
                    backend_response: Any = _coerce_lvs_response(json.loads(response_text))
                except json.JSONDecodeError:
                    backend_response = response_text
        except aiohttp.ClientError as e:
            logger.error(
                "LVS %s connection error: stream=%r media_id=%s url=%s error=%s",
                STREAM_SUMMARIZE_ENDPOINT,
                configured.media_name,
                configured.media_id,
                request_url,
                e,
            )
            return LVSStreamUnderstandingOutput(
                status=LVSMediaStatus.FAILED,
                stream_name=configured.media_name,
                stream_id=configured.media_id,
                configured=True,
                message=f"Failed to connect to LVS {STREAM_SUMMARIZE_ENDPOINT}: {e}",
            )

        return LVSStreamUnderstandingOutput(
            status=LVSMediaStatus.SUCCESS,
            stream_name=configured.media_name,
            stream_id=configured.media_id,
            configured=True,
            message=f"Here is the {lvs_input.response_type}.",
            content=backend_response,
            lvs_backend_response=backend_response,
        )

    yield FunctionInfo.create(
        single_fn=_lvs_stream_understanding,
        description=_lvs_stream_understanding.__doc__,
        input_schema=LVSStreamUnderstandingInput,
        single_output_schema=LVSStreamUnderstandingOutput,
    )

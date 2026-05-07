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

"""LVS media configuration tool."""

from collections.abc import AsyncGenerator
from enum import StrEnum
import json
import logging
from typing import Any
from typing import Literal

import aiohttp
from nat.builder.builder import Builder
from nat.builder.context import Context
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from nat.data_models.interactive import HumanPromptText
from nat.data_models.interactive import InteractionResponse
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

from vss_agents.tools.lvs_media_state import LVSConfiguredMedia
from vss_agents.tools.lvs_media_state import configured_media
from vss_agents.tools.lvs_media_state import remember_configured_media
from vss_agents.tools.vst.utils import get_stream_info_by_name

logger = logging.getLogger(__name__)


GENERATE_CAPTIONS_ENDPOINT = "/v1/generate_captions"
CAPTION_GENERATION_STARTED_MESSAGE = "Caption generation started. Please try again later."


class LVSMediaStatus(StrEnum):
    """Status values used by LVS media tools."""

    ABORTED = "aborted"
    ACCEPTED = "accepted"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"
    SUCCESS = "success"


DEFAULT_HITL_CONFIRMATION_TEMPLATE = """
Please review the above media configuration before it is sent to LVS.

Options:
- Press Submit (empty) to confirm and configure the media
- Type `/redo` to modify parameters
- Type `/cancel` to cancel configuration

Enter your choice or press Submit to proceed:"""


def _format_config_summary(scenario: str, events: list[str], objects_of_interest: list[str]) -> str:
    objects = ", ".join(objects_of_interest) if objects_of_interest else "None"
    return "\n".join(
        [
            "**Scenario:**",
            f"```\n{scenario}\n```",
            "",
            "**Events to Detect:**",
            f"```\n{', '.join(events)}\n```",
            "",
            "**Objects of Interest:**",
            f"```\n{objects}\n```",
        ]
    )


def _coerce_lvs_response(payload: Any) -> Any:
    """Unwrap direct JSON or OpenAI-style LVS responses."""
    if not isinstance(payload, dict):
        return payload

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, str):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return content

    return payload


async def _prompt_user_input(prompt_text: str, required: bool = True, placeholder: str = "") -> str:
    nat_context = Context.get()
    user_input_manager = nat_context.user_interaction_manager

    human_prompt = HumanPromptText(text=prompt_text, required=required, placeholder=placeholder)
    response: InteractionResponse = await user_input_manager.prompt_user_input(human_prompt)
    return str(response.content.text).strip()


class LVSConfigMediaConfig(FunctionBaseConfig, name="lvs_config_media"):
    """Configuration for the LVS media configuration tool."""

    lvs_backend_url: str = Field(..., description="The URL of the LVS backend service.")
    vst_internal_url: str = Field(..., description="Internal VST URL used to resolve live stream RTSP URLs.")
    model: str = Field(default="gpt-4o", description="VLM model to use for LVS media processing.")
    conn_timeout_ms: int = Field(default=5000, description="Connection timeout in milliseconds.")
    read_timeout_ms: int = Field(default=600000, description="Read timeout in milliseconds.")
    chunk_duration: int = Field(default=10, description="Duration of each stream chunk in seconds.")
    seed: int | None = Field(default=None, description="Random seed for LVS media processing.")
    vlm_input_width: int | None = Field(
        default=None,
        description="Optional VLM input frame width (pixels). When set, forwarded to LVS to bound the visual-token count.",
    )
    vlm_input_height: int | None = Field(
        default=None,
        description="Optional VLM input frame height (pixels). When set, forwarded to LVS to bound the visual-token count.",
    )
    hitl_scenario_template: str = Field(..., description="HITL template for collecting media scenario.")
    hitl_events_template: str = Field(..., description="HITL template for collecting media events.")
    hitl_objects_template: str = Field(..., description="HITL template for collecting objects of interest.")
    hitl_confirmation_template: str | None = Field(
        default=None,
        description="HITL template for final media configuration confirmation.",
    )
    default_scenario: str = Field(default="", description="Default media scenario.")
    default_events: list[str] = Field(default_factory=list, description="Default media events.")

    model_config = ConfigDict(extra="forbid")


class LVSConfigMediaInput(BaseModel):
    """Input for configuring media in LVS."""

    media_type: Literal["stream"] = Field(
        default="stream", description="Media type to configure. Currently stream only."
    )
    stream_name: str = Field(..., description="The VST live stream/camera name to configure for LVS.")

    @field_validator("stream_name")
    @classmethod
    def validate_stream_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("stream_name cannot be empty")
        return value.strip()


class LVSConfigMediaOutput(BaseModel):
    """Output from the LVS media configuration tool."""

    status: LVSMediaStatus = Field(..., description="Configuration status.")
    media_type: Literal["stream"] = Field(..., description="Configured media type.")
    media_name: str = Field(..., description="Configured media name.")
    media_id: str | None = Field(default=None, description="Configured media ID.")
    configured: bool = Field(default=False, description="Whether the media is configured in short-term memory.")
    message: str = Field(..., description="User-facing status message.")
    scenario: str | None = Field(default=None, description="Configured scenario.")
    events: list[str] | None = Field(default=None, description="Configured events.")
    objects_of_interest: list[str] | None = Field(default=None, description="Configured objects of interest.")
    lvs_backend_response: Any | None = Field(default=None, description="Raw LVS backend response, if any.")

    @property
    def summary(self) -> str | None:
        """Final-answer hint consumed by the top agent after configuration attempts."""
        if self.status in {LVSMediaStatus.ACCEPTED, LVSMediaStatus.ABORTED, LVSMediaStatus.FAILED}:
            return self.message
        return None


@register_function(config_type=LVSConfigMediaConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def lvs_config_media(config: LVSConfigMediaConfig, _: Builder) -> AsyncGenerator[FunctionInfo]:
    """Configure media for LVS summarization."""

    async def _collect_hitl_parameters(
        current_params: tuple[str, list[str], list[str]] | None = None,
    ) -> tuple[str, list[str], list[str]] | None:
        cancel_info = "\n\nNote: Type `/cancel` at any time to abort media configuration."

        if current_params is not None:
            current_scenario, current_events, current_objects = current_params
            scenario_prompt = f"CURRENTLY SET: `{current_scenario}`\n\n{config.hitl_scenario_template}{cancel_info}"
            events_prompt = (
                f"CURRENTLY SET: `{', '.join(current_events)}`\n\n{config.hitl_events_template}{cancel_info}"
            )
            objects_current = ", ".join(current_objects) if current_objects else "None"
            objects_prompt = f"CURRENTLY SET: `{objects_current}`\n\n{config.hitl_objects_template}{cancel_info}"
        else:
            current_scenario = config.default_scenario
            current_events = config.default_events
            current_objects = []
            scenario_prefix = f"DEFAULT: `{current_scenario}`\n\n" if current_scenario else ""
            events_prefix = f"DEFAULT: `{', '.join(current_events)}`\n\n" if current_events else ""
            scenario_prompt = f"{scenario_prefix}{config.hitl_scenario_template}{cancel_info}"
            events_prompt = f"{events_prefix}{config.hitl_events_template}{cancel_info}"
            objects_prompt = f"{config.hitl_objects_template}{cancel_info}"

        scenario = ""
        while not scenario:
            user_input = await _prompt_user_input(
                scenario_prompt,
                required=not bool(current_scenario),
                placeholder="e.g., warehouse monitoring or /cancel",
            )
            if user_input.lower() == "/cancel":
                return None
            if not user_input and current_scenario:
                scenario = current_scenario
            elif user_input:
                scenario = user_input

        events: list[str] = []
        while not events:
            user_input = await _prompt_user_input(
                events_prompt,
                required=not bool(current_events),
                placeholder="e.g., accident, forklift stuck or /cancel",
            )
            if user_input.lower() == "/cancel":
                return None
            if not user_input and current_events:
                events = current_events
            elif user_input:
                events = [event.strip() for event in user_input.split(",") if event.strip()]

        user_input = await _prompt_user_input(
            objects_prompt,
            required=False,
            placeholder='e.g., forklifts, workers OR "skip" or /cancel',
        )
        if user_input.lower() == "/cancel":
            return None
        if user_input.lower() == "skip":
            objects_of_interest: list[str] = []
        elif not user_input and current_objects:
            objects_of_interest = current_objects
        elif user_input:
            objects_of_interest = [obj.strip() for obj in user_input.split(",") if obj.strip()]
        else:
            objects_of_interest = []

        return scenario, events, objects_of_interest

    async def _confirm_config(scenario: str, events: list[str], objects_of_interest: list[str]) -> str:
        hitl_template = config.hitl_confirmation_template or DEFAULT_HITL_CONFIRMATION_TEMPLATE
        prompt_text = f"{_format_config_summary(scenario, events, objects_of_interest)}\n\n{hitl_template}"
        return (
            await _prompt_user_input(
                prompt_text,
                required=False,
                placeholder="/redo, /cancel, or press Submit to proceed",
            )
        ).lower()

    async def _lvs_config_media(lvs_input: LVSConfigMediaInput) -> LVSConfigMediaOutput:
        """
        Set up a live stream for LVS caption generation.

        Trigger: call this tool ONLY when the user explicitly asks to start caption
        generation for a stream (e.g. "start summarizing the stream <name>",
        "start captioning <name>", "set up stream <name>"). Do NOT call this tool
        speculatively or in response to another tool's "not_configured" message —
        the user must confirm first.

        For streams, this tool resolves the stream in VST, collects scenario,
        events, and objects_of_interest through HITL, calls LVS
        `/v1/generate_captions`, and stores the configured stream in short-term
        memory so later `lvs_stream_understanding` / report calls can succeed.
        """
        media_name = lvs_input.stream_name
        logger.info("Configuring LVS %s '%s'", lvs_input.media_type, media_name)

        media_id, media_url = await get_stream_info_by_name(media_name, config.vst_internal_url)
        if not media_id or not media_url:
            return LVSConfigMediaOutput(
                status=LVSMediaStatus.FAILED,
                media_type=lvs_input.media_type,
                media_name=media_name,
                message=f"Stream '{media_name}' was not found in VST live streams.",
            )

        configured = configured_media(lvs_input.media_type, media_name)
        current_params = None
        if configured:
            current_params = (
                configured.scenario,
                list(configured.events),
                list(configured.objects_of_interest),
            )

        while True:
            params = await _collect_hitl_parameters(current_params)
            if params is None:
                return LVSConfigMediaOutput(
                    status=LVSMediaStatus.ABORTED,
                    media_type=lvs_input.media_type,
                    media_name=media_name,
                    media_id=media_id,
                    configured=False,
                    message="Media configuration was cancelled by user.",
                )

            scenario, events, objects_of_interest = params
            choice = await _confirm_config(scenario, events, objects_of_interest)
            if choice == "/redo":
                current_params = (scenario, events, objects_of_interest)
                continue
            if choice == "/cancel":
                return LVSConfigMediaOutput(
                    status=LVSMediaStatus.ABORTED,
                    media_type=lvs_input.media_type,
                    media_name=media_name,
                    media_id=media_id,
                    configured=False,
                    message="Media configuration was cancelled by user.",
                )
            break

        payload: dict[str, Any] = {
            "id": media_id,
            "model": config.model,
            "scenario": scenario,
            "events": events,
            "chunk_duration": config.chunk_duration,
        }
        if config.seed is not None:
            payload["seed"] = config.seed
        if config.vlm_input_width is not None:
            payload["vlm_input_width"] = config.vlm_input_width
        if config.vlm_input_height is not None:
            payload["vlm_input_height"] = config.vlm_input_height
        request_url = f"{config.lvs_backend_url.rstrip('/')}{GENERATE_CAPTIONS_ENDPOINT}"
        logger.info(
            "LVS %s request: media=%r media_id=%s url=%s payload=%s",
            GENERATE_CAPTIONS_ENDPOINT,
            media_name,
            media_id,
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
                    "LVS %s response: media=%r media_id=%s status=%d body=%s",
                    GENERATE_CAPTIONS_ENDPOINT,
                    media_name,
                    media_id,
                    response.status,
                    response_text,
                )
                if response.status not in (200, 201, 202):
                    return LVSConfigMediaOutput(
                        status=LVSMediaStatus.FAILED,
                        media_type=lvs_input.media_type,
                        media_name=media_name,
                        media_id=media_id,
                        configured=False,
                        message=f"LVS {GENERATE_CAPTIONS_ENDPOINT} failed with status {response.status}: {response_text}",
                    )

                try:
                    backend_response: Any | None = (
                        _coerce_lvs_response(json.loads(response_text)) if response_text else None
                    )
                except json.JSONDecodeError:
                    backend_response = response_text
        except aiohttp.ClientError as e:
            logger.error(
                "LVS %s connection error: media=%r media_id=%s url=%s error=%s",
                GENERATE_CAPTIONS_ENDPOINT,
                media_name,
                media_id,
                request_url,
                e,
            )
            return LVSConfigMediaOutput(
                status=LVSMediaStatus.FAILED,
                media_type=lvs_input.media_type,
                media_name=media_name,
                media_id=media_id,
                configured=False,
                message=f"Failed to connect to LVS {GENERATE_CAPTIONS_ENDPOINT}: {e}",
            )

        remember_configured_media(
            LVSConfiguredMedia(
                media_type=lvs_input.media_type,
                media_name=media_name,
                media_id=media_id,
                media_url=media_url,
                scenario=scenario,
                events=tuple(events),
                objects_of_interest=tuple(objects_of_interest),
            )
        )

        return LVSConfigMediaOutput(
            status=LVSMediaStatus.ACCEPTED,
            media_type=lvs_input.media_type,
            media_name=media_name,
            media_id=media_id,
            configured=True,
            message=CAPTION_GENERATION_STARTED_MESSAGE,
            scenario=scenario,
            events=events,
            objects_of_interest=objects_of_interest,
            lvs_backend_response=backend_response,
        )

    yield FunctionInfo.create(
        single_fn=_lvs_config_media,
        description=_lvs_config_media.__doc__,
        input_schema=LVSConfigMediaInput,
        single_output_schema=LVSConfigMediaOutput,
    )

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
LVS Video Understanding Tool with Mandatory HITL Prompt Configuration.

This tool wraps the LVS (Long Video Summarization) service API to provide
video understanding capabilities for long videos. It has a similar interface
to the video_understanding tool but uses LVS's chunk-based processing.

Key features:
- Uses LVS service for hierarchical summarization (chunk-based processing)
- Better suited for long videos (> 2 minutes)
- MANDATORY Human-in-the-Loop (HITL) prompt configuration before every analysis
- Prompts come from config and can be accepted or overridden by user during HITL
- User must explicitly accept or modify all 3 prompts before video analysis begins
"""

from collections.abc import AsyncGenerator
from enum import StrEnum
import json
import logging

import aiohttp
from nat.builder.builder import Builder
from nat.builder.context import Context
from nat.builder.context import ContextState
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from nat.data_models.interactive import HumanPromptText
from nat.data_models.interactive import InteractionResponse
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from vss_agents.utils.url_translation import translate_url

logger = logging.getLogger(__name__)


class LVSStatus(StrEnum):
    """Status values for LVS video understanding operations."""

    ABORTED = "aborted"
    SUCCESS = "success"


# Default HITL confirmation template
DEFAULT_HITL_CONFIRMATION_TEMPLATE = """
Please review the above configuration that will be sent for video analysis:

**Options:**
• Press Submit (empty) → Confirm and proceed with video analysis
• Type `/redo` → Modify parameters
• Type `/cancel` → Cancel analysis

Enter your choice or press Submit to proceed:"""


class LVSVideoUnderstandingConfig(FunctionBaseConfig, name="lvs_video_understanding"):
    """Configuration for the LVS Video Understanding tool."""

    lvs_backend_url: str = Field(
        ...,
        description="The URL of the LVS backend service (e.g., http://localhost:38111).",
    )

    # Timeout configuration
    conn_timeout_ms: int = Field(
        default=5000,
        description="Connection timeout in milliseconds for LVS API calls.",
    )

    read_timeout_ms: int = Field(
        default=600000,  # 10 minutes for long videos
        description="Read timeout in milliseconds for LVS API calls.",
    )

    model: str = Field(
        default="gpt-4o",
        description="LVS model to use for video analysis.",
    )

    # Video URL tool for getting video URL from sensor ID
    video_url_tool: str = Field(
        default="vst_video_url",
        description="A tool to be used to get the video URL by sensor ID and timestamp (default to use VST service)",
    )

    # API Parameters (configurable from config file)
    response_format_type: str = Field(
        default="text",
        description="Response format type (e.g., 'text', 'json')",
    )

    enable_chat: bool = Field(
        default=False,
        description="Enable chat mode for LVS",
    )

    enable_cv_metadata: bool = Field(
        default=False,
        description="Enable computer vision metadata in response",
    )

    temperature: float = Field(
        default=0.4,
        description="Temperature for LLM sampling (0.0 to 1.0)",
    )

    seed: int | None = Field(
        default=1,
        description="Random seed for reproducibility",
    )

    top_p: float = Field(
        default=1.0,
        description="Top-p (nucleus) sampling parameter",
    )

    top_k: int = Field(
        default=10,
        description="Top-k sampling parameter",
    )

    max_tokens: int = Field(
        default=512,
        description="Maximum tokens in response",
    )

    chunk_duration: int = Field(
        default=10,
        description="Duration of each video chunk in seconds (0 = entire video in one request)",
    )

    num_frames_per_chunk: int = Field(
        default=20,
        description="Number of frames to sample per chunk",
    )

    enable_audio: bool = Field(
        default=False,
        description="Enable audio processing",
    )

    stream: bool = Field(
        default=True,
        description="Enable streaming response",
    )

    include_usage: bool = Field(
        default=True,
        description="Include usage statistics in response",
    )

    # HITL Templates (mandatory - configured in YAML)
    hitl_scenario_template: str = Field(
        ...,
        description="HITL template for collecting scenario from user",
    )

    hitl_events_template: str = Field(
        ...,
        description="HITL template for collecting events from user",
    )

    hitl_objects_template: str = Field(
        ...,
        description="HITL template for collecting objects_of_interest from user",
    )

    hitl_confirmation_template: str | None = Field(
        default=None,
        description="HITL template for final confirmation before video analysis. If None, uses default template.",
    )

    # Default values for HITL parameters
    default_scenario: str = Field(
        default="",
        description="Default scenario to use when no persistent state exists (e.g., 'traffic monitoring')",
    )

    default_events: list[str] = Field(
        default_factory=list,
        description="Default events list to use when no persistent state exists (e.g., ['accident', 'pedestrian crossing'])",
    )

    # URL translation configuration for VLM
    vlm_mode: str = Field(
        default="local",
        description="VLM mode: 'remote' (VLM is external, needs public URLs), 'local' or 'local_shared' (VLM is local, needs internal URLs)",
    )
    internal_ip: str = Field(
        default="",
        description="Internal IP / docker host IP for URL translation",
    )
    external_ip: str = Field(
        default="",
        description="Public IP accessible from the internet for URL translation",
    )
    vst_internal_url: str | None = Field(
        default=None,
        description="Internal VST base URL (e.g., 'http://HOST_IP:30888'). "
        "Used for URL translation when behind a reverse proxy.",
    )

    model_config = ConfigDict(extra="forbid")


class LVSVideoUnderstandingInput(BaseModel):
    """Input for the LVS Video Understanding tool with mandatory HITL."""

    sensor_id: str = Field(
        ...,
        description="The sensor ID of the video to understand.",
        min_length=1,
    )


@register_function(config_type=LVSVideoUnderstandingConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def lvs_video_understanding(
    config: LVSVideoUnderstandingConfig, builder: Builder
) -> AsyncGenerator[FunctionInfo]:
    """
    LVS Video Understanding Tool with HITL for Scenario, Events, and Objects.

    This tool uses the LVS (Long Video Summarization) service to analyze videos
    and supports Human-in-the-Loop configuration of analysis parameters.

    HITL collects:
    - scenario (REQUIRED): Description of the video scenario
    - events (REQUIRED): List of events to detect
    - objects_of_interest (OPTIONAL): List of objects to focus on

    Parameters are persisted per conversation thread.
    """

    logger.info(f"Initializing LVS Video Understanding tool (backend: {config.lvs_backend_url})")

    # Persistent state: maps thread_id -> (scenario, events, objects_of_interest)
    lvs_params_state: dict[str, tuple[str, list[str], list[str]]] = {}

    async def _prompt_user_input(prompt_text: str, required: bool = True, placeholder: str = "") -> str:
        """
        Prompt user for input using HITL.

        Args:
            prompt_text: The prompt text to show to the user
            required: Whether the input is required
            placeholder: Placeholder text for the input

        Returns:
            str: User's input
        """
        nat_context = Context.get()
        user_input_manager = nat_context.user_interaction_manager

        human_prompt = HumanPromptText(text=prompt_text, required=required, placeholder=placeholder)
        response: InteractionResponse = await user_input_manager.prompt_user_input(human_prompt)
        response_text: str = str(response.content.text).strip()

        return response_text

    def _format_lvs_config_summary(
        scenario: str,
        events: list[str],
        objects_of_interest: list[str],
    ) -> str:
        """
        Format a summary of LVS configuration for user review.

        Args:
            scenario: The scenario description
            events: List of events to detect
            objects_of_interest: List of objects to focus on

        Returns:
            str: Formatted configuration summary
        """
        summary_lines = [
            "**Scenario:**",
            f"```\n{scenario}\n```",
            "",
            "**Events to Detect:**",
            f"```\n{', '.join(events)}\n```",
            "",
        ]

        if objects_of_interest:
            summary_lines.extend(
                [
                    "**Objects of Interest:**",
                    f"```\n{', '.join(objects_of_interest)}\n```",
                ]
            )
        else:
            summary_lines.extend(
                [
                    "**Objects of Interest:**",
                    "```\nNone\n```",
                ]
            )

        return "\n".join(summary_lines)

    async def _confirm_lvs_request(
        scenario: str,
        events: list[str],
        objects_of_interest: list[str],
    ) -> str:
        """
        Show all LVS configuration and get user confirmation.

        Args:
            scenario: The scenario description
            events: List of events to detect
            objects_of_interest: List of objects to focus on

        Returns:
            str: Normalized user choice ("/redo", "/cancel", or empty string for proceed)
        """
        config_summary = _format_lvs_config_summary(scenario, events, objects_of_interest)

        hitl_template = config.hitl_confirmation_template or DEFAULT_HITL_CONFIRMATION_TEMPLATE
        prompt_text = f"{config_summary}\n\n{hitl_template}"

        user_choice = await _prompt_user_input(
            prompt_text,
            required=False,
            placeholder="/redo, /cancel, or press Submit to proceed",
        )

        # Return normalized choice
        return user_choice.lower().strip()

    async def _collect_hitl_parameters(
        current_params: tuple[str, list[str], list[str]] | None = None,
    ) -> tuple[str, list[str], list[str]] | None:
        """
        Collect scenario, events, and objects_of_interest via HITL.

        If current_params is provided, shows current values and allows user to accept or modify.
        User can type /cancel at any step to abort.

        Args:
            current_params: Optional current parameters (scenario, events, objects_of_interest)

        Returns:
            tuple: (scenario, events, objects_of_interest), or None if cancelled
        """
        logger.info("Starting HITL parameter collection workflow")

        # Cancel info to append to each prompt
        cancel_info = "\n\n**Note:** Type `/cancel` at any time to abort the video analysis."

        # Build prompt with current values if they exist
        if current_params:
            current_scenario, current_events, current_objects = current_params
            scenario_prompt = f"**CURRENTLY SET:** `{current_scenario}`\n\n{config.hitl_scenario_template}{cancel_info}"
            events_prompt = (
                f"**CURRENTLY SET:** `{', '.join(current_events)}`\n\n{config.hitl_events_template}{cancel_info}"
            )
            if current_objects:
                objects_prompt = (
                    f"**CURRENTLY SET:** `{', '.join(current_objects)}`\n\n{config.hitl_objects_template}{cancel_info}"
                )
            else:
                objects_prompt = f"**CURRENTLY SET:** None\n\n{config.hitl_objects_template}{cancel_info}"
        else:
            # Use default values from config when no persistent state exists
            current_scenario = config.default_scenario
            current_events = config.default_events
            current_objects = []  # Always empty by default

            if current_scenario or current_events:
                # Show defaults if they exist
                if current_scenario:
                    scenario_prompt = (
                        f"**DEFAULT:** `{current_scenario}`\n\n{config.hitl_scenario_template}{cancel_info}"
                    )
                else:
                    scenario_prompt = f"{config.hitl_scenario_template}{cancel_info}"

                if current_events:
                    events_prompt = (
                        f"**DEFAULT:** `{', '.join(current_events)}`\n\n{config.hitl_events_template}{cancel_info}"
                    )
                else:
                    events_prompt = f"{config.hitl_events_template}{cancel_info}"

                objects_prompt = f"{config.hitl_objects_template}{cancel_info}"
            else:
                # No defaults configured
                scenario_prompt = f"{config.hitl_scenario_template}{cancel_info}"
                events_prompt = f"{config.hitl_events_template}{cancel_info}"
                objects_prompt = f"{config.hitl_objects_template}{cancel_info}"

        # Collect scenario (REQUIRED)
        scenario = ""
        while not scenario:
            user_input = await _prompt_user_input(
                scenario_prompt,
                required=not bool(current_scenario),  # Not required if we have a current value
                placeholder="e.g., traffic monitoring or /cancel",
            )

            # Check for /cancel
            if user_input and user_input.strip().lower() == "/cancel":
                logger.info("User cancelled during scenario collection")
                return None

            if not user_input and current_scenario:
                scenario = current_scenario
                logger.info(f"User accepted current scenario: {scenario}")
            elif user_input:
                scenario = user_input
                logger.info(f"User provided new scenario: {scenario}")
            else:
                logger.warning("Scenario is required, prompting again")

        # Collect events (REQUIRED)
        events: list[str] = []
        while not events:
            user_input = await _prompt_user_input(
                events_prompt,
                required=not bool(current_events),  # Not required if we have current values
                placeholder="e.g., accident, pedestrian crossing or /cancel",
            )

            # Check for /cancel
            if user_input and user_input.strip().lower() == "/cancel":
                logger.info("User cancelled during events collection")
                return None

            # If user pressed Enter and we have current values, use them
            if not user_input and current_events:
                events = current_events
                logger.info(f"User accepted current events: {events}")
            elif user_input:
                events = [e.strip() for e in user_input.split(",") if e.strip()]
                logger.info(f"User provided new events: {events}")
            else:
                logger.warning("Events are required, prompting again")

        # Collect objects_of_interest (OPTIONAL - requires explicit "skip" to skip)
        user_input = await _prompt_user_input(
            objects_prompt,
            required=False,
            placeholder='e.g., cars, trucks, pedestrians OR type "skip" to skip or /cancel',
        )

        # Check for /cancel
        if user_input and user_input.strip().lower() == "/cancel":
            logger.info("User cancelled during objects collection")
            return None

        # Check if user explicitly typed "skip"
        if user_input.lower() == "skip":
            objects_of_interest = []
            logger.info("User explicitly skipped objects_of_interest")
        elif not user_input and current_objects:
            # Empty input with existing state -> keep current values
            objects_of_interest = current_objects
            logger.info(f"User accepted current objects_of_interest: {objects_of_interest}")
        elif user_input:
            # User provided new values
            objects_of_interest = [o.strip() for o in user_input.split(",") if o.strip()]
            logger.info(f"User provided new objects_of_interest: {objects_of_interest}")
        else:
            # Empty input with no existing state -> require explicit input
            logger.warning("Please provide objects_of_interest or type 'skip' to skip")
            objects_of_interest = []

        logger.info("HITL parameter collection completed")
        return scenario, events, objects_of_interest

    async def _lvs_video_understanding(lvs_input: LVSVideoUnderstandingInput) -> str:
        """
        Use LVS(Long Video Summarization) service to understand and summarize a video.

        This tool is optimized for long videos and uses
        chunk-based processing with event detection.


        Args:
            lvs_input: LVSVideoUnderstandingInput with sensor_id(sensor name or video file name in VST)
        Returns:
            str: The summary/analysis from LVS service
        """
        # Get thread_id for state persistence
        thread_id = ContextState.get().conversation_id.get()
        logger.info(f"Processing LVS request for thread {thread_id}")

        logger.info(f"LVS Video Understanding: Processing '{lvs_input.sensor_id}'")

        # Get current parameters for this thread (if any)
        current_params = lvs_params_state.get(thread_id)

        if current_params:
            logger.info(f"Found existing parameters for thread {thread_id}")
        else:
            logger.info(f"No existing parameters for thread {thread_id}, will collect new ones")

        # Initialize variables for type checker
        scenario: str = ""
        events: list[str] = []
        objects_of_interest: list[str] = []

        # HITL workflow with confirmation loop
        while True:
            # Step 1: Collect parameters via HITL
            logger.info("Running HITL workflow to collect/confirm parameters")
            params_result = await _collect_hitl_parameters(current_params)

            # Handle cancellation
            if params_result is None:
                logger.info("LVS analysis cancelled by user during parameter collection")
                return json.dumps(
                    {
                        "status": LVSStatus.ABORTED.value,
                        "message": "Video analysis was cancelled by user.",
                    },
                    indent=2,
                )

            scenario, events, objects_of_interest = params_result

            # Step 2: Show all configs and get confirmation (before fetching video URL)
            logger.info("Showing LVS configuration for user confirmation")
            user_choice = await _confirm_lvs_request(scenario, events, objects_of_interest)

            if user_choice == "/redo":
                # User wants to modify parameters - loop back with current values
                logger.info("User requested redo - restarting parameter collection")
                current_params = (scenario, events, objects_of_interest)
                continue
            elif user_choice == "/cancel":
                # User cancelled
                logger.info("LVS analysis cancelled by user")
                return json.dumps(
                    {
                        "status": LVSStatus.ABORTED.value,
                        "message": "Video analysis was cancelled by user.",
                    },
                    indent=2,
                )
            else:
                # Empty string or any other input - proceed with LVS request
                logger.info("User confirmed - proceeding with LVS analysis")
                break

        # Store HITL parameters for later inclusion in the report
        hitl_scenario = scenario
        hitl_events = events
        hitl_objects_of_interest = objects_of_interest

        # Update state for this thread
        lvs_params_state[thread_id] = (scenario, events, objects_of_interest)
        logger.info(f"Updated parameters state for thread {thread_id}")

        # Load video URL tool (deferred to runtime to avoid initialization order issues)
        logger.info(f"Loading video URL tool: {config.video_url_tool}")
        video_url_tool = await builder.get_tool(config.video_url_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

        # Get video URL using the video_url_tool (e.g., vst_video_url)
        logger.info(f"Using {config.video_url_tool} to get video URL for file {lvs_input.sensor_id}")
        video_url_args = {
            "sensor_id": lvs_input.sensor_id,
        }
        logger.debug(f"Video URL tool arguments: {video_url_args}")
        video_url_result = await video_url_tool.ainvoke(input=video_url_args)
        video_url = video_url_result.video_url

        # Translate URL for VLM based on vlm_mode:
        # - remote: INTERNAL_IP -> EXTERNAL_IP (VLM needs public URLs)
        # - local/local_shared: EXTERNAL_IP -> INTERNAL_IP (VLM needs internal URLs)
        video_url = translate_url(
            video_url,
            config.vlm_mode,
            config.internal_ip,
            config.external_ip,
            config.vst_internal_url,
        )
        logger.info(f"[LVS Video Understanding] VIDEO URL FOR VLM ANALYSIS: {video_url}")

        # Build LVS request using new API contract
        lvs_request = {
            "url": video_url,
            "model": config.model,
            # HITL parameters
            "scenario": scenario,
            "events": events,
            # Video processing parameters
            "chunk_duration": config.chunk_duration,
            "num_frames_per_chunk": config.num_frames_per_chunk,
        }
        logger.info(f"LVS request: {lvs_request}")

        # Add seed if configured
        if config.seed is not None:
            lvs_request["seed"] = config.seed

        if objects_of_interest:
            lvs_request["objects_of_interest"] = objects_of_interest

        logger.info(f"Calling LVS service: {config.lvs_backend_url}/summarize")
        logger.debug(f"LVS request: {lvs_request}")

        # Call LVS service
        try:
            timeout = aiohttp.ClientTimeout(connect=config.conn_timeout_ms / 1000, total=config.read_timeout_ms / 1000)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(f"{config.lvs_backend_url}/summarize", json=lvs_request) as response,
            ):
                if response.status != 200:
                    error_text = await response.text()
                    raise RuntimeError(f"LVS service returned {response.status}: {error_text}")

                response_json = await response.json()

                # Parse OpenAI-style response format: choices[0].message.content contains JSON string
                content = response_json["choices"][0]["message"]["content"]
                content_json = json.loads(content)
                logger.info(f"LVS response: {content_json}")
                video_summary = content_json.get("video_summary", "").strip()
                events = content_json.get("events", [])

                # Generate friendly message only if no summary and no events
                if not video_summary and not events:
                    video_summary = "No significant events or activities were detected in this video."
                    logger.warning("LVS returned no summary and no events")
                elif not video_summary:
                    logger.info(f"LVS returned no summary but has {len(events)} events")
                if not events:
                    logger.warning("LVS returned no events")

                result = {
                    "video_summary": video_summary,
                    "events": events,
                    "hitl_prompts": {
                        "scenario": hitl_scenario,
                        "events": hitl_events,
                        "objects_of_interest": hitl_objects_of_interest,
                    },
                    "lvs_backend_response": response_json,
                }
                if not video_summary and not events:
                    result["note"] = (
                        "The video may not contain the types of events specified in the search criteria, or the content may not be clear enough for detection."
                    )
                formatted_response = json.dumps(result, indent=2, ensure_ascii=False)
                logger.info(f"LVS response received with {len(events)} events")
                return formatted_response

        except aiohttp.ClientError as e:
            logger.error(f"LVS service connection error: {e}")
            raise RuntimeError(f"Failed to connect to LVS service: {e}") from e
        except Exception as e:
            logger.error(f"LVS video understanding failed: {e}")
            raise

    yield FunctionInfo.create(
        single_fn=_lvs_video_understanding,
        description=_lvs_video_understanding.__doc__,
        input_schema=LVSVideoUnderstandingInput,
        single_output_schema=str,
    )

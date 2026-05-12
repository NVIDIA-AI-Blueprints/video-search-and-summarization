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
"""Video sampling and VLM payload settings loaded from NAT VLM (llms) profiles."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic import Field

_VLM_VIDEO_FIELD_NAMES = (
    "max_frames",
    "max_fps",
    "min_pixels",
    "max_pixels",
    "use_base64",
    "reasoning",
)


class VlmVideoUnderstandingSettings(BaseModel):
    """Subset of VLM profile fields consumed by the ``video_understanding`` tool."""

    max_frames: int = Field(
        24,
        description="The maximum number of frames to sample from the video",
    )
    max_fps: int = Field(
        default=2,
        description="Maximum frames per second to sample. num_frames = min(video_length * max_fps, max_frames)",
    )
    min_pixels: int = Field(
        1568,
        description="The minimum number of pixels for 2 frames from the video, 28x28=784 will be converted to one video token",
    )
    max_pixels: int = Field(
        345600,
        description="The maximum number of pixels for 2 frames from the video, 28x28=784 will be converted to one video token",
    )
    reasoning: bool = Field(
        False,
        description="Only for cosmos reason models, turn on reasoning when you want to let the VLM reason before returning the answer.",
    )
    use_base64: bool = Field(
        False,
        description="Whether to use base64 encoding to send the video to the VLM. If True, the video will be encoded to base64 and sent to the VLM.",
    )


def vlm_video_settings_from_llm_config(llm_config: Any) -> VlmVideoUnderstandingSettings:
    """Build settings from a validated LLM config (``nim`` / ``openai`` / etc.), using defaults for unset keys."""
    raw_dump: dict[str, Any] = llm_config.model_dump(mode="python")
    picked: dict[str, Any] = {}
    for key in _VLM_VIDEO_FIELD_NAMES:
        if key not in raw_dump:
            continue
        val = raw_dump[key]
        if val is None:
            continue
        picked[key] = val
    return VlmVideoUnderstandingSettings.model_validate(picked)

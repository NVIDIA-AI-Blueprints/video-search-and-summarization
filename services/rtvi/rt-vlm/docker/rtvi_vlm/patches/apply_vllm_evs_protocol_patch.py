# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Install the public EVS session protocol compatibility types into vLLM.

The public RT-VLM image ships the eleven IP-sensitive EVS implementations as
architecture-specific extension modules.  Those modules import their OpenAI
request and response schemas from ``entrypoints.openai.engine.protocol``.
The stock vLLM 0.17.1 module does not contain those schemas, so install this
non-sensitive API compatibility layer before the protected extensions.
"""

from __future__ import annotations

import os
from pathlib import Path


VLLM_ROOT = Path(
    os.environ.get("VLLM_EVS_TARGET", "/usr/local/lib/python3.12/dist-packages/vllm")
)
PROTOCOL = VLLM_ROOT / "entrypoints/openai/engine/protocol.py"
MARKER = "# RTVI EVS session protocol compatibility types"


EVS_PROTOCOL_TYPES = r'''

# RTVI EVS session protocol compatibility types
class EvsAdvancedConfig(OpenAIBaseModel):
    """Statistical tuning knobs for event detection."""

    min_clips: int | None = None
    max_recording_clips: int | None = None
    spike_std_k: float | None = None
    settling_std_k: float | None = None
    std_floor_ratio: float | None = None
    downward_baseline_min: float | None = None
    decision_lag: int | None = None


class VideoSessionSamplingParams(OpenAIBaseModel):
    """Decode parameters for a session's event-gated generations."""

    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=-1)
    min_p: float | None = Field(default=None, ge=0.0, le=1.0)
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    frequency_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    repetition_penalty: float | None = Field(default=None, gt=0.0)
    seed: int | None = None
    ignore_eos: bool | None = None
    min_tokens: int | None = Field(default=None, ge=0)
    stop: list[str] | str | None = None
    stop_token_ids: list[int] | None = None

    def to_sampling_params_config(self, default_max_tokens: int = 1024) -> dict[str, Any]:
        cfg = self.model_dump(exclude_none=True)
        cfg.setdefault("max_tokens", default_max_tokens)
        return cfg


def build_session_sampling_params(
    config: dict[str, Any] | None, default_max_tokens: int = 1024
) -> SamplingParams:
    """Expand persisted session sampling settings into vLLM parameters."""
    return SamplingParams(**(config or {"max_tokens": default_max_tokens}))


class VideoSessionCreateRequest(OpenAIBaseModel):
    """Request to create an EVS video analysis session."""

    model: str
    token_budget: int = 8192
    prompt: str = ""
    system_prompt: str | None = None
    timestamp_prompt_template: str | None = None
    sampling_params: VideoSessionSamplingParams | None = None
    event_only: bool = False
    event_chunk_duration_s: float | None = None
    event_ema_memory_s: float | None = None
    event_advanced: EvsAdvancedConfig | None = None


class VideoSessionCreateResponse(OpenAIBaseModel):
    """Response after creating a video session."""

    session_id: str


class VideoClipAddRequest(OpenAIBaseModel):
    """Request to add a video clip to a session."""

    video_url: dict[str, str]
    mm_processor_kwargs: dict[str, Any] | None = None
    timestamps: list[float] | None = None
    is_last: bool = False
    chunk_id: int | None = None


class VideoClipResponse(OpenAIBaseModel):
    """Response after adding a video clip to a session."""

    clip_id: str
    tokens_used: int
    tokens_remaining: int
    frames_kept: int
    frames_dropped: int
    kept_timestamps: list[float] = []
    stashed: bool = False
    generated: bool = False
    generate_trigger: str | None = None
    response_text: str | None = None
    response_usage: dict | None = None
    boundary_pending: bool = False
    event_started: bool = False
    idle_discarded: bool = False
    round_timestamps: list[float] = []
'''


def main() -> None:
    if not PROTOCOL.is_file():
        raise FileNotFoundError(f"vLLM OpenAI protocol module not found: {PROTOCOL}")

    content = PROTOCOL.read_text()
    if MARKER in content:
        print(f"EVS protocol compatibility types already installed in {PROTOCOL}")
        return

    PROTOCOL.write_text(content.rstrip() + EVS_PROTOCOL_TYPES + "\n")
    print(f"Installed EVS protocol compatibility types in {PROTOCOL}")


if __name__ == "__main__":
    main()

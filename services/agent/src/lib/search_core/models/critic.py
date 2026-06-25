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
"""Critic-agent input/output models.

Mirrors services/agent/src/vss_agents/agents/critic_agent.py:156-200.
Re-homes the enum and result classes from inside critic_agent.py — they were
imported by tools/search.py:1320-1322 and live with the critic logically.

Critic ships as EXPERIMENTAL in v1 (DESIGN.md §6.4): wire format may change
before promotion to stable.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

# Used as a Pydantic field annotation (``CriticAgentInput.videos``) — must
# be importable at runtime so Pydantic can resolve the stringified ref.
from .common import VideoInfo  # noqa: TC001  Pydantic-resolved at runtime

# Wire-shared format selector — 'iso' for ISO 8601 UTC strings, 'offset' for
# seconds-since-stream-start. Used by the NAT shim, the library primitive, and
# the VLM analyzer protocol so the three stay in lockstep.
TimeFormat = Literal["iso", "offset"]


class CriticAgentResult(StrEnum):
    """Verdict produced by the critic agent for a single video clip.

    Mirrors services/agent/src/vss_agents/agents/critic_agent.py:168-173.
    """

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNVERIFIED = "unverified"


class CriticAgentInput(BaseModel):
    """Input for CriticAgent.run(): a query plus the candidate videos to verify."""

    model_config = ConfigDict(extra="forbid")
    query: str
    videos: list[VideoInfo]
    evaluation_count: int | None = Field(
        default=None,
        ge=1,
        description="Optional cap on how many videos to evaluate (saves VLM calls).",
    )


class VideoResult(BaseModel):
    """Result for a single video evaluation."""

    model_config = ConfigDict(extra="forbid")
    video_info: VideoInfo
    result: CriticAgentResult
    criteria_met: dict[str, bool] | None = None  # None = critic produced no per-criterion verdict


class CriticAgentOutput(BaseModel):
    """Bulk result envelope from CriticAgent.run()."""

    model_config = ConfigDict(extra="forbid")
    video_results: list[VideoResult] = Field(default_factory=list)

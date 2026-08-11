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

Houses the critic's enum and result classes alongside the critic logic they
belong to, rather than inline with the orchestrator that consumes them.

Critic ships as EXPERIMENTAL in v1: the wire format may change before it is
promoted to stable.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  Pydantic field annotation
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

# Wire-shared format selector — 'iso' for ISO 8601 UTC strings, 'offset' for
# seconds-since-stream-start. Shared so the primitive and the VLM analyzer
# protocol agree on how timestamps are expressed.
TimeFormat = Literal["iso", "offset"]


class VideoInfo(BaseModel):
    """A hashable video segment identified by sensor and time bounds."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sensor_id: str
    start_timestamp: datetime
    end_timestamp: datetime
    #: Ingest kind of the source these bounds came from. Only ``"video_file"``
    #: is indexed on the synthetic midnight-anchored epoch that has to be
    #: rebased onto VST's real replay timeline. Left unset the bounds are taken
    #: literally, which is the safe default: rebasing wall-clock bounds that
    #: merely fall outside the current timeline would verify a *different*
    #: clip and return a confident verdict about footage the caller never
    #: retrieved.
    source_type: str | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> VideoInfo:
        if self.end_timestamp <= self.start_timestamp:
            raise ValueError("end_timestamp must be after start_timestamp")
        return self


class CriticAgentResult(StrEnum):
    """Verdict produced by the critic agent for a single video clip."""

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNVERIFIED = "unverified"


class CriticAgentInput(BaseModel):
    """Input for CriticAgent.run(): a query plus the candidate videos to verify."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1)
    videos: list[VideoInfo]
    evaluation_count: int | None = Field(
        default=None,
        ge=1,
        description="Optional cap on how many videos to evaluate (saves VLM calls).",
    )

    @model_validator(mode="after")
    def validate_query(self) -> CriticAgentInput:
        if not self.query.strip():
            raise ValueError("query must be non-empty")
        return self


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

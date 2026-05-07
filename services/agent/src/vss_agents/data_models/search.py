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
"""Shared data contract for the ``search`` orchestrator's I/O."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class DecomposedQuery(BaseModel):
    """Result of query decomposition."""

    query: str = Field(default="", description="The main search query")
    video_sources: list[str] = Field(default_factory=list, description="List of video source names")
    source_type: str = Field(default="video_file", description="Type of source: 'rtsp' or 'video_file'")
    timestamp_start: str | None = Field(default=None, description="Start timestamp in ISO format")
    timestamp_end: str | None = Field(default=None, description="End timestamp in ISO format")
    attributes: list[str] = Field(default_factory=list, description="List of attributes to filter by")
    has_action: bool | None = Field(
        default=None,
        description="True if query contains an action/event/activity, False if only visual/physical attributes",
    )
    object_ids: list[int] | None = Field(
        default=None, description="List of integer object IDs if explicitly mentioned in the query"
    )
    top_k: int | None = Field(default=None, description="Number of results to return")


class SearchInput(BaseModel):
    """Input for the Search tool"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        description="Description of the item to search from",
    )

    source_type: Literal["rtsp", "video_file"] = Field(
        ...,
        description="Type of video source: 'rtsp' for live streams or 'video_file' for uploaded video files.",
    )

    video_sources: list[str] | None = Field(
        default=None,
        description="A list of video names to search from. In DevEx, these are VST sensor-names. Defaults to search from all videos.",
    )

    description: str | None = Field(
        default=None,
        description="Description of video's metadata data, for example, the location of the camera, the category of videos. Defaults to match all descriptions.",
    )

    timestamp_start: datetime | None = Field(
        default=None,
        description="Start time of the video, ISO timestamp. Note for uploaded videos, as a convention, we use 2025-01-01T00:00:00 as the start time.",
    )

    timestamp_end: datetime | None = Field(
        default=None,
        description="End time of the video, ISO timestamp. Note for uploaded videos, as a convention, we use 2025-01-01T00:00:00 as the start time.",
    )

    top_k: int | None = Field(
        default=None,
        description="Number of returned videos. If not provided, returns all matching results.",
    )

    min_cosine_similarity: float = Field(
        default=0.0,
        description="Minimum cosine similarity to filter non-agent embed-only search results. Default is 0.",
    )

    agent_mode: bool = Field(
        ...,
        description="Whether or not backend shall use an agent(LLM) to analyze/decompose the input query and fill in parameters",
    )

    use_critic: bool = Field(
        default=True,
        description="""Request-level flag to enable/disable critic agent for this search request.
        `critic_agent` must be set and `enable_critic` must be True in the config.""",
    )


class CriticResult(BaseModel):
    """Structured verdict from the critic agent for a single search result."""

    result: str = Field(description="Critic verdict: 'confirmed', 'rejected', or 'unverified'.")
    criteria_met: dict[str, bool] = Field(
        default_factory=dict,
        description="Per-criterion evaluation from the critic (e.g. {'person': true, 'walking': false}).",
    )


# FIXME: sensor_id is not the same as stream_id, but for now they have the same value.
# We'll need to revisit this code once we begin to differentiate between them.
class SearchResult(BaseModel):
    """A single search result item"""

    video_name: str = Field(..., description="Name of the video")
    description: str = Field(..., description="Description of the video")
    start_time: str = Field(..., description="Start time of the video in ISO timestamp format")
    end_time: str = Field(..., description="End time of the video in ISO timestamp format")
    sensor_id: str = Field(..., description="Sensor ID (e.g., 21908c9a-bd40-4941-8a2e-79bc0880fb5a)")
    screenshot_url: str = Field(..., description="URL to access the screenshot")
    similarity: float = Field(..., description="Cosine similarity score")
    object_ids: list[str] = Field(
        default_factory=list, description="List of object IDs for video generation (from attribute search)"
    )
    critic_result: CriticResult | None = Field(
        default=None,
        description="Critic agent verdict for this result. None if the critic was not run.",
    )

    # Generalized fusion path additive fields
    # Note: Downstream consumers can branch on `fused_score is not None` to detect the new path
    # TODO: in the future, revisit model to replace this with a more meaningful ratio for easy interpretation
    fused_score: float | None = Field(
        default=None,
        description=(
            "Rank-derived voting score from the fusion NAT tool. "
            "Unitless and varies by method/k/space-count - interpret as a *ratio* of the "
            "theoretical max, not as an absolute."
        ),
    )
    contributing_spaces: list[str] = Field(
        default_factory=list,
        description=(
            "Embedding spaces (e.g. 'embed', 'attribute', 'caption') that contributed to "
            "this result via the generalized fusion tool."
        ),
    )


__all__ = [
    "CriticResult",
    "DecomposedQuery",
    "SearchInput",
    "SearchResult",
]

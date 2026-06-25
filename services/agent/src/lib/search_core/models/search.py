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
"""Search-orchestrator input/output models.

Faithful port of services/agent/src/vss_agents/tools/search.py:1571-1671 with
one deliberate omission: SearchInput does NOT carry use_attribute_search —
that lives in SearchOptions (DESIGN.md §3) because it is an orchestrator
config-time flag, not a user-routable knob.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  Pydantic field annotation; resolved at runtime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

# ``SourceType`` and ``datetime`` are used in Pydantic field annotations
# below; Pydantic v2 resolves the stringified annotations at model_build
# time and needs them importable at runtime.
from .common import SourceType  # noqa: TC001  Pydantic-resolved at runtime


class SearchInput(BaseModel):
    """User-facing input for the Search orchestrator."""

    model_config = ConfigDict(extra="forbid")

    query: str
    original_query: str | None = None
    source_type: SourceType
    video_sources: list[str] | None = None
    description: str | None = None
    timestamp_start: datetime | None = None
    timestamp_end: datetime | None = None
    top_k: int | None = None
    attributes: list[str] = Field(default_factory=list)
    has_action: bool | None = None
    object_ids: list[int] | None = None
    # Cosine similarity is in [-1, 1]; the UI sends negative thresholds for
    # low-confidence searches, so don't clamp the lower bound to 0.
    min_cosine_similarity: float = Field(default=0.0, ge=-1.0, le=1.0)
    agent_mode: bool  # required, matches tools/search.py
    use_critic: bool = True


class CriticResult(BaseModel):
    """Per-search-result verdict from the critic agent.

    Mirrors tools/search.py:1628-1636. Values use the same enum vocabulary
    as CriticAgentResult — confirmed / rejected / unverified.
    """

    model_config = ConfigDict(extra="forbid")
    result: str
    criteria_met: dict[str, bool] = Field(default_factory=dict)


class SearchResult(BaseModel):
    """A single search result item.

    Mirrors tools/search.py:1640-1657.
    """

    model_config = ConfigDict(extra="forbid")
    video_name: str
    description: str
    start_time: str
    end_time: str
    sensor_id: str
    screenshot_url: str
    similarity: float
    object_ids: list[str] = Field(default_factory=list)
    critic_result: CriticResult | None = None


class SearchOutput(BaseModel):
    """Output envelope from Search.run()."""

    model_config = ConfigDict(extra="forbid")
    data: list[SearchResult] = Field(default_factory=list)
    search_messages: list[str] = Field(default_factory=list)

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
"""Shared Pydantic models used across multiple primitives."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  Pydantic field annotation; resolved at runtime
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict

# Library tightens AttributeSearchInput.source_type from `str` to this Literal —
# see DESIGN.md §5.3 "Tightening". The NAT shim widens back to plain str at the
# wire to preserve today's behavior.
SourceType = Literal["video_file", "rtsp"]


class VideoInfo(BaseModel):
    """A video segment identified by sensor and time bounds.

    Used by CriticAgentInput.videos and by the orchestrator when handing
    candidates to the critic for VLM verification.

    `frozen=True` makes instances hashable so they work as dict keys / set
    members — matches the NAT-side `agents/critic_agent.py` shape, which
    tools/search.py:1380-1393 relies on for de-duplication of critic verdicts.

    Pydantic v2 coerces ISO 8601 strings to datetime automatically, so wire
    inputs from `SearchResult.start_time` (string) construct cleanly.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    sensor_id: str
    start_timestamp: datetime
    end_timestamp: datetime

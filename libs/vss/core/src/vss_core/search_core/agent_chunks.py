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
"""AgentMessageChunk / AgentMessageChunkType — streaming-chunk types.

Consumers ``isinstance``-check chunks yielded during orchestrator streaming, so
there must be exactly ONE class object shared across every import site. This
module is the canonical home; compatibility namespaces re-import these same
names rather than redefining them, keeping the identity check valid.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel
from pydantic import Field


class AgentMessageChunkType(enum.StrEnum):
    """Type of the message chunk."""

    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    SUBAGENT_CALL = "subagent_call"
    ERROR = "error"
    FINAL = "final"


class AgentMessageChunk(BaseModel):
    """Message chunk yielded during orchestrator streaming."""

    type: AgentMessageChunkType = Field(AgentMessageChunkType.THOUGHT, description="The type of the message chunk")
    content: str = Field("", description="The content of the message chunk")

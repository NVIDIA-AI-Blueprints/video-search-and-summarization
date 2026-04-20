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

import enum
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import Field

# ========== EXISTING ENUMS AND MODELS ==========


class AgentDecision(str, enum.Enum):
    """Decision of the agent"""

    TOOL = "tool"
    END = "finished"
    AGENT = "agent"
    SUPERVISOR = "supervisor"


class AgentMessageChunkType(str, enum.Enum):
    """Type of the message chunk"""

    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    SUBAGENT_CALL = "subagent_call"
    ERROR = "error"
    FINAL = "final"


class AgentMessageChunk(BaseModel):
    """Message chunk for the Report Agent"""

    type: AgentMessageChunkType = Field(AgentMessageChunkType.THOUGHT, description="The type of the message chunk")
    content: str = Field("", description="The content of the message chunk")


class AgentOutput(BaseModel):
    """
    Standardized output model for agents (report_agent, multi_report_agent, etc.).

    This model provides:
      - messages: Conversational responses to the user
      - side_effects: Generated artifacts (HTML reports, PDFs, charts, media URLs)
      - metadata: Execution information (timing, tool calls, confidence, etc.)
      - status: Execution status indicator
      - error_message: Error details if applicable
    """

    messages: list[str] = Field(default_factory=list, description="Conversational output messages for the user")

    side_effects: dict[str, Any] = Field(
        default_factory=dict,
        description="UI rendering artifacts and generated outputs. May include 'report_html', 'report_pdf_url', "
        "'report_markdown_url', 'snapshot_urls', 'video_urls', 'charts', 'chart_html', 'formatted_incidents', etc.",
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Execution metadata such as 'incident_count', 'generation_time_ms', "
        "'tools_called', 'agent_iterations', 'confidence', etc.",
    )

    status: Literal["success", "partial_success", "error"] = Field(
        default="success", description="Status of the agent execution"
    )

    error_message: str | None = Field(
        default=None, description="Error message if status is 'error' or 'partial_success'"
    )


# ========== NOTE ==========
# ReportMode and ReportAgentInput are specific to report_agent.py
# MultiReportAgentInput is specific to multi_report_agent.py
# AgentOutput is shared by both report_agent and multi_report_agent

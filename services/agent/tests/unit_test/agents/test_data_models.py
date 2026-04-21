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
"""Tests for vss_agents/agents/data_models.py."""

from vss_agents.agents.data_models import AgentDecision
from vss_agents.agents.data_models import AgentMessageChunk
from vss_agents.agents.data_models import AgentMessageChunkType
from vss_agents.agents.data_models import AgentOutput


class TestAgentDecision:
    """Tests for AgentDecision enum."""

    def test_agent_decision_values(self):
        """Test AgentDecision enum values."""
        assert AgentDecision.TOOL.value == "tool"
        assert AgentDecision.END.value == "finished"
        assert AgentDecision.AGENT.value == "agent"
        assert AgentDecision.SUPERVISOR.value == "supervisor"

    def test_agent_decision_is_string_enum(self):
        """Test that AgentDecision is a string enum."""
        assert isinstance(AgentDecision.TOOL, str)
        assert AgentDecision.TOOL == "tool"


class TestAgentMessageChunkType:
    """Tests for AgentMessageChunkType enum."""

    def test_message_chunk_type_values(self):
        """Test AgentMessageChunkType enum values."""
        assert AgentMessageChunkType.THOUGHT.value == "thought"
        assert AgentMessageChunkType.TOOL_CALL.value == "tool_call"
        assert AgentMessageChunkType.SUBAGENT_CALL.value == "subagent_call"
        assert AgentMessageChunkType.ERROR.value == "error"
        assert AgentMessageChunkType.FINAL.value == "final"


class TestAgentMessageChunk:
    """Tests for AgentMessageChunk model."""

    def test_create_message_chunk_defaults(self):
        """Test creating AgentMessageChunk with defaults."""
        chunk = AgentMessageChunk()
        assert chunk.type == AgentMessageChunkType.THOUGHT
        assert chunk.content == ""

    def test_create_message_chunk_with_values(self):
        """Test creating AgentMessageChunk with values."""
        chunk = AgentMessageChunk(
            type=AgentMessageChunkType.TOOL_CALL,
            content="Calling video_caption tool",
        )
        assert chunk.type == AgentMessageChunkType.TOOL_CALL
        assert chunk.content == "Calling video_caption tool"

    def test_message_chunk_all_types(self):
        """Test AgentMessageChunk with all types."""
        for chunk_type in AgentMessageChunkType:
            chunk = AgentMessageChunk(type=chunk_type, content=f"Test {chunk_type}")
            assert chunk.type == chunk_type

    def test_message_chunk_long_content(self):
        """Test AgentMessageChunk with long content."""
        long_content = "A" * 10000
        chunk = AgentMessageChunk(content=long_content)
        assert chunk.content == long_content


class TestAgentOutput:
    """Tests for AgentOutput model."""

    def test_create_agent_output_defaults(self):
        """Test creating AgentOutput with defaults."""
        output = AgentOutput()
        assert output.messages == []
        assert output.side_effects == {}
        assert output.metadata == {}
        assert output.status == "success"
        assert output.error_message is None

    def test_create_agent_output_success(self):
        """Test creating successful AgentOutput."""
        output = AgentOutput(
            messages=["Analysis complete", "Found 3 incidents"],
            side_effects={"report_html": "<html>...</html>"},
            metadata={"generation_time_ms": 1500, "tools_called": ["video_caption"]},
            status="success",
        )
        assert len(output.messages) == 2
        assert "report_html" in output.side_effects
        assert output.metadata["generation_time_ms"] == 1500

    def test_create_agent_output_error(self):
        """Test creating error AgentOutput."""
        output = AgentOutput(
            messages=[],
            status="error",
            error_message="Failed to process video",
        )
        assert output.status == "error"
        assert output.error_message == "Failed to process video"

    def test_create_agent_output_partial_success(self):
        """Test creating partial success AgentOutput."""
        output = AgentOutput(
            messages=["Partial results available"],
            status="partial_success",
            error_message="Some tools failed",
        )
        assert output.status == "partial_success"
        assert output.error_message == "Some tools failed"

    def test_agent_output_status_literal(self):
        """Test AgentOutput status accepts only valid literals."""
        # Valid statuses
        for status in ["success", "partial_success", "error"]:
            output = AgentOutput(status=status)
            assert output.status == status

    def test_agent_output_side_effects_types(self):
        """Test AgentOutput side_effects with various value types."""
        output = AgentOutput(
            side_effects={
                "report_html": "<html>Report</html>",
                "snapshot_urls": ["http://url1", "http://url2"],
                "charts": [{"title": "Chart 1", "data": [1, 2, 3]}],
                "incident_count": 5,
            }
        )
        assert isinstance(output.side_effects["report_html"], str)
        assert isinstance(output.side_effects["snapshot_urls"], list)
        assert isinstance(output.side_effects["incident_count"], int)

    def test_agent_output_metadata_types(self):
        """Test AgentOutput metadata with various value types."""
        output = AgentOutput(
            metadata={
                "generation_time_ms": 1500,
                "tools_called": ["tool1", "tool2"],
                "confidence": 0.95,
                "agent_iterations": 3,
            }
        )
        assert output.metadata["generation_time_ms"] == 1500
        assert len(output.metadata["tools_called"]) == 2
        assert output.metadata["confidence"] == 0.95

    def test_agent_output_empty_error_message(self):
        """Test AgentOutput with empty error message."""
        output = AgentOutput(status="error", error_message="")
        assert output.error_message == ""

    def test_agent_output_messages_types(self):
        """Test AgentOutput messages list."""
        messages = [
            "Starting analysis...",
            "Processing video frames",
            "Analysis complete",
        ]
        output = AgentOutput(messages=messages)
        assert output.messages == messages

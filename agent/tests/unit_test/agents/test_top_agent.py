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
"""Unit tests for top_agent module."""

import pytest
from vss_agents.agents.top_agent import EMPTY_MESSAGES_ERROR
from vss_agents.agents.top_agent import EMPTY_SCRATCHPAD_ERROR
from vss_agents.agents.top_agent import NO_INPUT_ERROR_MESSAGE
from vss_agents.agents.top_agent import TOOL_NOT_FOUND_ERROR_MESSAGE
from vss_agents.agents.top_agent import strip_frontend_tags


class TestTopAgentConstants:
    """Test top_agent module constants."""

    def test_tool_not_found_error_message(self):
        assert "{tool_name}" in TOOL_NOT_FOUND_ERROR_MESSAGE
        assert "{tools}" in TOOL_NOT_FOUND_ERROR_MESSAGE

    def test_no_input_error_message(self):
        assert "No human input" in NO_INPUT_ERROR_MESSAGE

    def test_empty_messages_error(self):
        assert "current_message" in EMPTY_MESSAGES_ERROR

    def test_empty_scratchpad_error(self):
        assert "agent_scratchpad" in EMPTY_SCRATCHPAD_ERROR


class TestStripFrontendTags:
    """Test strip_frontend_tags function."""

    @pytest.mark.parametrize(
        "content,expected",
        [
            # HTML img with alt - should remain unchanged
            (
                'Check this <img src="http://example.com/img.jpg" alt="Snapshot at 00:05" width="400"> image',
                'Check this <img src="http://example.com/img.jpg" alt="Snapshot at 00:05" width="400"> image',
            ),
            # Self-closing img with alt - should remain unchanged
            (
                '<img src="http://example.com/chart.png" alt="Incident Chart" />',
                '<img src="http://example.com/chart.png" alt="Incident Chart" />',
            ),
            # Markdown image - should remain unchanged
            (
                "Here is ![Incident Snapshot](http://example.com/img.jpg) the image",
                "Here is ![Incident Snapshot](http://example.com/img.jpg) the image",
            ),
            # Markdown link - should remain unchanged
            (
                "Download [PDF Report](http://example.com/report.pdf) here",
                "Download [PDF Report](http://example.com/report.pdf) here",
            ),
            # Both markdown image and link - should remain unchanged
            (
                "![Snapshot](http://img.jpg) and [Video](http://video.mp4)",
                "![Snapshot](http://img.jpg) and [Video](http://video.mp4)",
            ),
            # Incidents tag - should be replaced
            (
                'Data: <incidents>{"incidents": [{"id": "123"}]}</incidents> end',
                "Data: [Incident data] end",
            ),
            # Multiline incidents tag - should be replaced
            (
                'Before\n<incidents>\n{\n  "incidents": [{"id": "123"}]\n}\n</incidents>\nAfter',
                "Before\n[Incident data]\nAfter",
            ),
            # No tags
            (
                "Plain text without any tags",
                "Plain text without any tags",
            ),
            # Empty content
            ("", ""),
            # Complex message with multiple elements - only incidents should be replaced
            (
                "Report generated successfully\n**Report Downloads:**\n- [Markdown Report](http://example.com/report.md)\n- [PDF Report](http://example.com/report.pdf)\n\n**Media:**\n- ![Incident Snapshot](http://example.com/snapshot.jpg)\n- [Incident Video](http://example.com/video.mp4)\n",
                "Report generated successfully\n**Report Downloads:**\n- [Markdown Report](http://example.com/report.md)\n- [PDF Report](http://example.com/report.pdf)\n\n**Media:**\n- ![Incident Snapshot](http://example.com/snapshot.jpg)\n- [Incident Video](http://example.com/video.mp4)\n",
            ),
        ],
    )
    def test_strip_frontend_tags(self, content, expected):
        assert strip_frontend_tags(content) == expected

    def test_none_content_returns_empty(self):
        assert strip_frontend_tags(None) == ""

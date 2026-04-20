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
"""Unit tests for report_gen module."""

from pydantic import ValidationError
import pytest
from vss_agents.tools.report_gen import ReportGenConfig
from vss_agents.tools.report_gen import ReportGenInput
from vss_agents.tools.report_gen import ReportGenOutput
from vss_agents.tools.report_gen import _format_messages_to_markdown


class TestReportGenConfig:
    """Test ReportGenConfig model."""

    def test_with_required_field(self):
        config = ReportGenConfig(object_store="test-object-store")
        assert config.object_store == "test-object-store"
        assert config.output_dir == "/tmp/agent_reports"
        assert config.base_url is None
        assert config.save_local_copy is True
        assert config.template_path == ""
        assert config.llm_name == ""
        assert config.template_name is None
        assert config.report_prompt == ""

    def test_custom_values(self):
        config = ReportGenConfig(
            object_store="custom-store",
            output_dir="/custom/reports",
            base_url="http://example.com",
            save_local_copy=False,
            template_path="templates/report.html",
            llm_name="openai_llm",
            template_name="incident_report.html",
            report_prompt="Generate a report based on {messages} using {template}",
        )
        assert config.output_dir == "/custom/reports"
        assert config.base_url == "http://example.com"
        assert config.save_local_copy is False
        assert config.template_path == "templates/report.html"
        assert config.llm_name == "openai_llm"
        assert config.template_name == "incident_report.html"
        assert "{messages}" in config.report_prompt


class TestReportGenInput:
    """Test ReportGenInput model."""

    def test_with_string_messages(self):
        input_data = ReportGenInput(messages="This is a summary report")
        assert input_data.messages == "This is a summary report"

    def test_with_list_messages(self):
        messages = [
            {"role": "user", "content": "What happened?"},
            {"role": "assistant", "content": "An incident occurred."},
        ]
        input_data = ReportGenInput(messages=messages)
        assert len(input_data.messages) == 2

    def test_with_empty_list(self):
        input_data = ReportGenInput(messages=[])
        assert input_data.messages == []

    def test_missing_messages_fails(self):
        with pytest.raises(ValidationError):
            ReportGenInput()


class TestReportGenOutput:
    """Test ReportGenOutput model."""

    def test_output_creation(self):
        output = ReportGenOutput(
            local_file_path="/tmp/reports/report_001.md",
            http_url="http://localhost:8000/static/reports/report_001.md",
            object_store_key="reports/report_001.md",
            summary="Incident report for sensor-001",
            file_size=1024,
            content="# Report\n\nThis is the report content.",
        )
        assert output.local_file_path == "/tmp/reports/report_001.md"
        assert output.http_url == "http://localhost:8000/static/reports/report_001.md"
        assert output.object_store_key == "reports/report_001.md"
        assert output.summary == "Incident report for sensor-001"
        assert output.file_size == 1024
        assert "# Report" in output.content

    def test_output_serialization(self):
        output = ReportGenOutput(
            local_file_path="/tmp/report.md",
            http_url="http://localhost/report.md",
            object_store_key="report.md",
            summary="Test summary",
            file_size=512,
            content="Test content",
        )
        data = output.model_dump()
        assert "local_file_path" in data
        assert "http_url" in data
        assert "object_store_key" in data
        assert "summary" in data
        assert "file_size" in data
        assert "content" in data


class TestFormatMessagesToMarkdown:
    """Test _format_messages_to_markdown function."""

    def test_format_empty_messages(self):
        result = _format_messages_to_markdown([])
        assert "# Deep Search Report" in result
        assert "Generated:" in result

    def test_format_dict_messages(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = _format_messages_to_markdown(messages)
        assert "# Deep Search Report" in result
        assert "Message 1" in result
        assert "Message 2" in result
        assert "dict" in result

    def test_format_string_message(self):
        messages = ["This is a string message"]
        result = _format_messages_to_markdown(messages)
        assert "# Deep Search Report" in result

    def test_format_object_with_content(self):
        class MessageLike:
            def __init__(self, content):
                self.content = content

        messages = [MessageLike("Test message content")]
        result = _format_messages_to_markdown(messages)
        assert "# Deep Search Report" in result

    def test_format_nested_content(self):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Complex content"}]},
        ]
        result = _format_messages_to_markdown(messages)
        assert "# Deep Search Report" in result

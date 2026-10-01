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
"""Unit tests for template_report_gen module."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
import pytest

from vss_agents.tools.template_report_gen import PDF_CONVERSION_AVAILABLE
from vss_agents.tools.template_report_gen import _build_authoritative_incident_facts
from vss_agents.tools.template_report_gen import _format_custom_report
from vss_agents.tools.template_report_gen import _get_object_store_url
from vss_agents.tools.template_report_gen import _run_vlm_analysis


class TestGetObjectStoreUrl:
    """Test _get_object_store_url function."""

    def test_s3_object_store(self):
        mock_store = MagicMock()
        mock_store.endpoint_url = "http://minio.example.com:9000"
        mock_store.bucket_name = "reports"

        mock_config = MagicMock()
        mock_config.base_url = "http://localhost:8000"

        result = _get_object_store_url(mock_store, "report.pdf", mock_config)
        assert result == "http://minio.example.com:9000/reports/report.pdf"

    def test_s3_object_store_with_trailing_slash(self):
        mock_store = MagicMock()
        mock_store.endpoint_url = "http://minio.example.com:9000/"
        mock_store.bucket_name = "bucket"

        mock_config = MagicMock()

        result = _get_object_store_url(mock_store, "file.pdf", mock_config)
        assert result == "http://minio.example.com:9000/bucket/file.pdf"

    def test_in_memory_store(self):
        mock_store = MagicMock(spec=[])  # No endpoint_url or bucket_name

        mock_config = MagicMock()
        mock_config.base_url = "http://localhost:8000/"

        result = _get_object_store_url(mock_store, "report.pdf", mock_config)
        assert result == "http://localhost:8000/report.pdf"

    def test_in_memory_store_base_url_no_trailing_slash(self):
        mock_store = MagicMock(spec=[])

        mock_config = MagicMock()
        mock_config.base_url = "http://localhost:8000"

        result = _get_object_store_url(mock_store, "test.pdf", mock_config)
        assert result == "http://localhost:8000/test.pdf"


class TestPdfConversionAvailable:
    """Test PDF conversion availability flag."""

    def test_pdf_conversion_flag_is_bool(self):
        assert isinstance(PDF_CONVERSION_AVAILABLE, bool)


class TestIncidentReportGrounding:
    """Regression coverage for grounding generated reports in incident data."""

    def test_selects_authoritative_incident_facts(self):
        facts = _build_authoritative_incident_facts(
            {
                "category": "Person in forklift aisle",
                "timestamp": "2026-09-29T06:11:30Z",
                "end": "2026-09-29T06:11:35Z",
                "sensorId": "Camera_01",
                "objectIds": ["person-7"],
                "info": {"primaryObjectId": "person-7"},
                "place": {"name": "Warehouse aisle 3"},
                # These unsupported fields must not become authoritative facts.
                "severity": "high",
                "injury": "possible",
            },
            alert_sensor_id="fallback-camera",
            alert_from_timestamp="fallback-start",
            alert_to_timestamp="fallback-end",
        )

        assert facts == {
            "category": "Person in forklift aisle",
            "timestamp": "2026-09-29T06:11:30Z",
            "end": "2026-09-29T06:11:35Z",
            "sensorId": "Camera_01",
            "objectIds": ["person-7"],
            "primaryObjectId": "person-7",
            "place.name": "Warehouse aisle 3",
        }

    @pytest.mark.asyncio
    async def test_vlm_prompt_forbids_unsupported_claims_and_resolves_empty_ids(self):
        vlm_tool = SimpleNamespace(ainvoke=AsyncMock(return_value="Only directly visible facts"))
        config = SimpleNamespace(vlm_prompts=["Describe people with object ids: {object_ids}"])
        report_input = SimpleNamespace(
            alert_sensor_id="Camera_01",
            alert_from_timestamp="2026-09-29T06:11:30Z",
            alert_to_timestamp="2026-09-29T06:11:35Z",
            vlm_reasoning=None,
        )

        result = await _run_vlm_analysis(report_input, vlm_tool, config, object_ids=[])

        assert result == ["Only directly visible facts"]
        prompt = vlm_tool.ainvoke.await_args.kwargs["input"]["user_prompt"]
        assert "{object_ids}" not in prompt
        assert "none provided" in prompt
        assert "Do not infer causes" in prompt
        assert "Do not invent identities" in prompt

    @pytest.mark.asyncio
    async def test_report_llm_receives_authoritative_facts_and_grounding_rules(self, tmp_path):
        template_name = "incident.md"
        (tmp_path / template_name).write_text("# Incident\n\n{detailed_description}", encoding="utf-8")
        captured = {}

        async def capture_prompt(prompt):
            captured["messages"] = prompt.to_messages()
            return AIMessage(content="# Incident\n\nN/A")

        content = await _format_custom_report(
            vlm_results=["A worker may have been injured."],
            alert_metadata={
                "category": "Person in forklift aisle",
                "timestamp": "2026-09-29T06:11:30Z",
                "sensorId": "Camera_01",
                "objectIds": ["person-7"],
                "place": {"name": "Warehouse aisle 3"},
            },
            alert_sensor_id="Camera_01",
            alert_from_timestamp="2026-09-29T06:11:30Z",
            alert_to_timestamp="2026-09-29T06:11:35Z",
            template_path=str(tmp_path),
            template_name=template_name,
            report_prompt="Populate this template:\n{template}\nVersion: {agent_version}",
            llm=RunnableLambda(capture_prompt),
        )

        assert content == "# Incident\n\nN/A"
        system_prompt = captured["messages"][0].content
        user_prompt = captured["messages"][1].content
        assert "Do not infer causes" in system_prompt
        assert "injuries" in system_prompt
        assert '"category": "Person in forklift aisle"' in user_prompt
        assert '"place.name": "Warehouse aisle 3"' in user_prompt
        assert "copy these values exactly" in user_prompt

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

from vss_agents.tools import template_report_gen as template_report_gen_module
from vss_agents.tools.template_report_gen import PDF_CONVERSION_AVAILABLE
from vss_agents.tools.template_report_gen import ReportContentValidationError
from vss_agents.tools.template_report_gen import _build_authoritative_incident_facts
from vss_agents.tools.template_report_gen import _format_custom_report
from vss_agents.tools.template_report_gen import _get_object_store_url
from vss_agents.tools.template_report_gen import _normalize_report_model_output
from vss_agents.tools.template_report_gen import _run_vlm_analysis
from vss_agents.tools.template_report_gen import _validate_report_body

_WAREHOUSE_TEMPLATE = """# Warehouse Incident Report

## Basic Information

| Field | Value |
|-------|-------|
| **Report Identifier** | {report_id} |

## Incident Details

| Field | Value |
|-------|-------|
| **Type of Incident** | {incident_type} |

## Location and Environment Details

| Field | Value |
|-------|-------|
| **Location Description** | {location_description} |

## People Involved

### Person {person_number}

| Field | Value |
|-------|-------|
| **Person Type** | {person_type} |

## Vehicles Involved

### Vehicle {vehicle_number}

| Field | Value |
|-------|-------|
| **Vehicle Type** | {vehicle_type} |
"""


def _valid_warehouse_report() -> str:
    return """# Warehouse Incident Report

## Basic Information

| Field | Value |
|-------|-------|
| **Report Identifier** | inc-123 |
| **Date of Incident** | 2026-09-29 (UTC) |
| **Time of Incident** | 06:11:30 UTC |
| **Reporting AI Agent** | report_agent |
| **Sensor ID** | Camera_01 |

## Incident Details

| Field | Value |
|-------|-------|
| **Type of Incident** | Person in forklift aisle |
| **Detailed Description** | A person walked through the forklift aisle. |
| **Safety Distance** | N/A |
| **Number of Persons involved** | 1 |
| **Number of Vehicles involved** | 0 |

## Location and Environment Details

| Field | Value |
|-------|-------|
| **Location Description** | Warehouse aisle 3 |
| **Light Condition** | Unknown |
| **Floor Condition** | Unknown |
| **Blockage** | N/A |

## People Involved

### Person 1

| Field | Value |
|-------|-------|
| **Person Type** | worker |
| **Person behaviour at the time** | walking |
| **Person Location** | aisle 3 |

## Vehicles Involved

### Vehicle 1

| Field | Value |
|-------|-------|
| **Vehicle Type** | N/A |
| **Vehicle maneuver at the time** | N/A |
| **Vehicle Location** | N/A |
"""


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
                "Id": "inc-123",
                "category": "Person in forklift aisle",
                "timestamp": "2026-09-29T06:11:30Z",
                "end": "2026-09-29T06:11:35Z",
                "sensorId": "Camera_01",
                "objectIds": ["person-7", "person-8"],
                "info": {"primaryObjectId": "person-7"},
                "place": {"name": "Warehouse aisle 3"},
                "people_count": 1,
                "vehicle_count": 0,
                # These unsupported fields must not become authoritative facts.
                "severity": "high",
                "injury": "possible",
            },
            alert_sensor_id="fallback-camera",
            alert_from_timestamp="fallback-start",
            alert_to_timestamp="fallback-end",
        )

        assert facts == {
            "Id": "inc-123",
            "category": "Person in forklift aisle",
            "timestamp": "2026-09-29T06:11:30Z",
            "end": "2026-09-29T06:11:35Z",
            "sensorId": "Camera_01",
            "objectIds": ["person-7", "person-8"],
            "primaryObjectId": "person-7",
            "place.name": "Warehouse aisle 3",
            "people_count": 1,
            "vehicle_count": 0,
        }
        # objectIds length must not be treated as a people/vehicle count.
        assert facts["people_count"] != len(facts["objectIds"])

    def test_primary_object_id_from_info_snake_case(self):
        facts = _build_authoritative_incident_facts(
            {"info": {"primary_object_id": "oid-9"}, "sensorId": "Camera_01"},
            alert_sensor_id="fallback",
            alert_from_timestamp="t0",
            alert_to_timestamp="t1",
        )
        assert facts["primaryObjectId"] == "oid-9"

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
        assert "Do not make unsupported claims about causes" in prompt
        assert "Do not invent identities" in prompt
        assert "audible when audio is available" in prompt
        assert "Actions, outcomes, and responses may be reported only when directly supported" in prompt

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
                "Id": "inc-123",
                "category": "Person in forklift aisle",
                "timestamp": "2026-09-29T06:11:30Z",
                "sensorId": "Camera_01",
                "objectIds": ["person-7"],
                "place": {"name": "Warehouse aisle 3"},
                "people_count": 1,
                "vehicle_count": 0,
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
        assert "Do not make unsupported claims about causes" in system_prompt
        assert "injuries" in system_prompt
        assert "timezone is stated explicitly" in system_prompt
        assert "audible when audio evidence is present" in system_prompt
        assert '"Id": "inc-123"' in user_prompt
        assert '"category": "Person in forklift aisle"' in user_prompt
        assert '"place.name": "Warehouse aisle 3"' in user_prompt
        assert '"people_count": 1' in user_prompt
        assert '"vehicle_count": 0' in user_prompt
        assert "copy these values exactly" in user_prompt


class TestReportBodyValidation:
    """Regression coverage for empty / thinking-only / resources-only report bodies."""

    def test_normalize_strips_thinking_and_fences(self):
        raw = "```markdown\n<think>plan</think>\n# Incident\n\nN/A\n```"
        assert _normalize_report_model_output(raw) == "# Incident\n\nN/A"

    def test_empty_output_rejected(self):
        with pytest.raises(ReportContentValidationError, match="empty_or_whitespace_body"):
            _validate_report_body("   \n", template_content="# Incident\n")

    def test_thinking_only_output_rejected(self):
        normalized = _normalize_report_model_output("<think>all reasoning, no report</think>")
        with pytest.raises(ReportContentValidationError, match="empty_or_whitespace_body"):
            _validate_report_body(normalized, template_content="# Incident\n")

    def test_resources_only_output_rejected(self):
        with pytest.raises(ReportContentValidationError, match="resources_only_body"):
            _validate_report_body(
                "##Resources\n\n**Incident Snapshot:** ![x](http://example.com/x.png)\n",
                template_content="# Incident\n",
            )

    def test_missing_warehouse_section_rejected(self):
        body = """# Warehouse Incident Report

## Basic Information

| Field | Value |
| **Report Identifier** | inc-1 |

## Incident Details

| Field | Value |
| **Type of Incident** | spill |
"""
        with pytest.raises(ReportContentValidationError, match="missing_required_section"):
            _validate_report_body(body, template_content=_WAREHOUSE_TEMPLATE)

    def test_empty_warehouse_section_rejected(self):
        body = """# Warehouse Incident Report

## Basic Information

| Field | Value |
| **Report Identifier** | inc-1 |

## Incident Details

| Field | Value |
| **Type of Incident** | spill |

## Location and Environment Details

| Field | Value |
| **Location Description** | aisle |

## People Involved

### Person {person_number}

| Field | Value |
| **Person Type** | {person_type} |

## Vehicles Involved

| Field | Value |
| **Vehicle Type** | N/A |
"""
        with pytest.raises(ReportContentValidationError, match="empty_required_section:People Involved"):
            _validate_report_body(body, template_content=_WAREHOUSE_TEMPLATE)

    def test_valid_warehouse_report_with_unknown_fields_accepted(self):
        _validate_report_body(_valid_warehouse_report(), template_content=_WAREHOUSE_TEMPLATE)

    def test_non_warehouse_template_skips_section_requirements(self):
        _validate_report_body("# Incident\n\nN/A", template_content="# Smart City Incident Report\n")

    @pytest.mark.asyncio
    async def test_validation_failure_prevents_resources_append(self, tmp_path, monkeypatch):
        template_name = "incident_report_template.md"
        (tmp_path / template_name).write_text(_WAREHOUSE_TEMPLATE, encoding="utf-8")
        append_calls: list[tuple] = []

        def _tracking_append(content, image_url, video_url):
            append_calls.append((content, image_url, video_url))
            return content

        monkeypatch.setattr(template_report_gen_module, "_append_resources_section", _tracking_append)

        async def empty_llm(_prompt):
            return AIMessage(content="<think>no usable body</think>")

        with pytest.raises(ReportContentValidationError, match="empty_or_whitespace_body"):
            await _format_custom_report(
                vlm_results=["visible person"],
                alert_metadata={"Id": "inc-1", "sensorId": "Camera_01"},
                alert_sensor_id="Camera_01",
                alert_from_timestamp="2026-09-29T06:11:30Z",
                alert_to_timestamp="2026-09-29T06:11:35Z",
                template_path=str(tmp_path),
                template_name=template_name,
                report_prompt="Populate:\n{template}\n{agent_version}",
                llm=RunnableLambda(empty_llm),
                image_url="http://example.com/snap.png",
                video_url="http://example.com/clip.mp4",
            )

        assert append_calls == []

    @pytest.mark.asyncio
    async def test_valid_warehouse_report_appends_resources(self, tmp_path):
        template_name = "incident_report_template.md"
        (tmp_path / template_name).write_text(_WAREHOUSE_TEMPLATE, encoding="utf-8")

        async def good_llm(_prompt):
            return AIMessage(content=_valid_warehouse_report())

        content = await _format_custom_report(
            vlm_results=["A person walked through the aisle."],
            alert_metadata={
                "Id": "inc-123",
                "category": "Person in forklift aisle",
                "sensorId": "Camera_01",
                "people_count": 1,
                "vehicle_count": 0,
            },
            alert_sensor_id="Camera_01",
            alert_from_timestamp="2026-09-29T06:11:30Z",
            alert_to_timestamp="2026-09-29T06:11:35Z",
            template_path=str(tmp_path),
            template_name=template_name,
            report_prompt="Populate:\n{template}\n{agent_version}",
            llm=RunnableLambda(good_llm),
            image_url="http://example.com/snap.png",
            video_url="http://example.com/clip.mp4",
        )

        assert "##Resources" in content
        assert "http://example.com/snap.png" in content
        assert content.index("## Basic Information") < content.index("##Resources")

    @pytest.mark.asyncio
    async def test_llm_exception_does_not_bypass_validation(self, tmp_path):
        template_name = "incident.md"
        (tmp_path / template_name).write_text("# Incident\n\n{x}", encoding="utf-8")

        async def boom(_prompt):
            raise RuntimeError("model unavailable")

        with pytest.raises(ValueError, match="Failed to generate custom report with LLM"):
            await _format_custom_report(
                vlm_results=["fallback text that must not be published"],
                alert_metadata={"sensorId": "Camera_01"},
                alert_sensor_id="Camera_01",
                alert_from_timestamp="t0",
                alert_to_timestamp="t1",
                template_path=str(tmp_path),
                template_name=template_name,
                report_prompt="Populate:\n{template}\n{agent_version}",
                llm=RunnableLambda(boom),
                image_url="http://example.com/snap.png",
            )

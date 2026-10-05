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
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
import pytest

from vss_agents.tools import template_report_gen as template_report_gen_module
from vss_agents.tools.template_report_gen import PDF_CONVERSION_AVAILABLE
from vss_agents.tools.template_report_gen import ReportContentValidationError
from vss_agents.tools.template_report_gen import TemplateReportGenConfig
from vss_agents.tools.template_report_gen import TemplateReportGenInput
from vss_agents.tools.template_report_gen import _build_authoritative_incident_facts
from vss_agents.tools.template_report_gen import _format_custom_report
from vss_agents.tools.template_report_gen import _get_object_store_url
from vss_agents.tools.template_report_gen import _normalize_report_model_output
from vss_agents.tools.template_report_gen import _run_vlm_analysis
from vss_agents.tools.template_report_gen import _validate_report_body
from vss_agents.tools.template_report_gen import template_report_gen


def _sample_table_report(**field_overrides: str) -> str:
    fields = {
        "Report Identifier": "inc-123",
        "Type of Incident": "Person in forklift aisle",
        "Detailed Description": "A person walked through the forklift aisle.",
        "Sensor ID": "Camera_01",
    }
    fields.update(field_overrides)
    rows = "\n".join(f"| **{label}** | {value}" for label, value in fields.items())
    return f"""# Custom Incident Report

## Summary

| Field | Value |
|-------|-------|
{rows}
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
    """Universal body checks plus optional explicit required_report_fields."""

    def test_required_report_fields_default_empty(self):
        config = TemplateReportGenConfig(
            object_store="object_store",
            llm_name="llm",
            video_understanding_tool="video_understanding",
        )
        assert config.required_report_fields == []
        assert config.required_report_sections == []

    def test_normalize_strips_thinking_and_fences(self):
        raw = "```markdown\n<think>plan</think>\n# Incident\n\nN/A\n```"
        assert _normalize_report_model_output(raw) == "# Incident\n\nN/A"

    def test_empty_output_rejected(self):
        with pytest.raises(ReportContentValidationError, match="empty_or_whitespace_body"):
            _validate_report_body("   \n")

    def test_thinking_only_output_rejected(self):
        normalized = _normalize_report_model_output("<think>all reasoning, no report</think>")
        with pytest.raises(ReportContentValidationError, match="empty_or_whitespace_body"):
            _validate_report_body(normalized)

    def test_unclosed_thinking_output_rejected(self):
        normalized = _normalize_report_model_output("<think>unclosed reasoning without end tag\n# Incident\n\nN/A")
        with pytest.raises(ReportContentValidationError, match="residual_thinking_markup"):
            _validate_report_body(normalized)

    def test_resources_only_output_rejected(self):
        with pytest.raises(ReportContentValidationError, match="resources_only_body"):
            _validate_report_body("##Resources\n\n**Incident Snapshot:** ![x](http://example.com/x.png)\n")

    def test_generic_mode_allows_renamed_and_removed_fields(self):
        body = """# Site Report

## Overview

A forklift paused near aisle 3. No people table is present.
"""
        _validate_report_body(body)

    def test_generic_mode_allows_changed_headings_and_prose(self):
        body = """# Freeform Narrative

### What happened
A person crossed the restricted lane.

### Who was nearby
- Worker near the pallet rack
- No vehicles observed
"""
        _validate_report_body(body)

    def test_generic_mode_allows_custom_people_vehicle_layout(self):
        body = """# Ops Note

People:
1. ID 7 — walking east

Vehicles: none observed
"""
        _validate_report_body(body)

    def test_generic_mode_allows_prompt_override_structure(self):
        # Report prompt may ask for a structure unrelated to the loaded template.
        body = "# Brief\n\nIncident summary only. Template sections intentionally omitted."
        _validate_report_body(body)

    def test_explicit_required_field_missing_rejected(self):
        body = _sample_table_report()
        body = body.replace("| **Detailed Description** | A person walked through the forklift aisle.\n", "")
        with pytest.raises(ReportContentValidationError, match="missing_required_field:Detailed Description"):
            _validate_report_body(body, required_report_fields=["Detailed Description"])

    def test_explicit_required_field_blank_rejected(self):
        body = _sample_table_report(**{"Detailed Description": "   "})
        with pytest.raises(ReportContentValidationError, match="empty_required_field:Detailed Description"):
            _validate_report_body(body, required_report_fields=["Detailed Description"])

    def test_explicit_required_field_placeholder_rejected_when_other_fields_populated(self):
        body = _sample_table_report(**{"Detailed Description": "{detailed_description}"})
        with pytest.raises(ReportContentValidationError, match="unresolved_placeholder:Detailed Description"):
            _validate_report_body(body, required_report_fields=["Detailed Description"])

    def test_unknown_elsewhere_does_not_satisfy_explicit_required_field(self):
        body = _sample_table_report(
            **{
                "Detailed Description": "{detailed_description}",
                "Type of Incident": "Unknown",
            }
        )
        with pytest.raises(ReportContentValidationError, match="unresolved_placeholder:Detailed Description"):
            _validate_report_body(body, required_report_fields=["Detailed Description"])

    def test_explicit_required_field_accepts_unknown_for_that_field(self):
        body = _sample_table_report(**{"Detailed Description": "Unknown"})
        _validate_report_body(body, required_report_fields=["Detailed Description"])

    def test_explicit_required_field_accepts_prose_label(self):
        body = "# Report\n\n**Detailed Description:** A person entered the aisle.\n"
        _validate_report_body(body, required_report_fields=["Detailed Description"])

    def test_rows_without_trailing_pipe_supported(self):
        body = """# Report

| Field | Value |
| **Detailed Description** | Resolved without trailing pipe
"""
        _validate_report_body(body, required_report_fields=["Detailed Description"])

    def test_missing_configured_section_rejected_when_description_is_present(self):
        body = _sample_table_report()
        with pytest.raises(ReportContentValidationError, match="missing_required_section:People Involved"):
            _validate_report_body(
                body,
                required_report_fields=["Detailed Description"],
                required_report_sections=["People Involved", "Vehicles Involved"],
            )

    def test_empty_configured_section_rejected(self):
        body = _sample_table_report() + "\n## People Involved\n\n## Vehicles Involved\n\nN/A\n"
        with pytest.raises(ReportContentValidationError, match="empty_required_section:People Involved"):
            _validate_report_body(
                body,
                required_report_sections=["People Involved", "Vehicles Involved"],
            )

    def test_placeholder_in_configured_section_rejected(self):
        body = (
            _sample_table_report()
            + "\n## People Involved\n\n### Person {person_number}\n\n| **Person Type** | worker\n"
            + "\n## Vehicles Involved\n\nN/A\n"
        )
        with pytest.raises(ReportContentValidationError, match="unresolved_placeholder:People Involved"):
            _validate_report_body(body, required_report_sections=["People Involved", "Vehicles Involved"])

    def test_configured_sections_accept_supported_content_or_section_level_na(self):
        body = (
            _sample_table_report()
            + "\n## People Involved\n\n### Person 1\n\n| **Person Type** | worker\n"
            + "\n## Vehicles Involved\n\nN/A\n"
        )
        _validate_report_body(
            body,
            required_report_fields=["Detailed Description"],
            required_report_sections=["People Involved", "Vehicles Involved"],
        )

    def test_omitted_sections_pass_when_not_configured(self):
        _validate_report_body(_sample_table_report())

    @pytest.mark.asyncio
    async def test_validation_failure_prevents_resources_append(self, tmp_path, monkeypatch):
        template_name = "custom.md"
        (tmp_path / template_name).write_text("# Custom\n\n{notes}", encoding="utf-8")
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
    async def test_custom_structure_appends_resources(self, tmp_path):
        template_name = "custom.md"
        (tmp_path / template_name).write_text("# Custom\n\n{notes}", encoding="utf-8")

        async def good_llm(_prompt):
            return AIMessage(content="# Brief\n\nCustom prose report with no warehouse sections.")

        content = await _format_custom_report(
            vlm_results=["A person walked through the aisle."],
            alert_metadata={"Id": "inc-123", "sensorId": "Camera_01"},
            alert_sensor_id="Camera_01",
            alert_from_timestamp="2026-09-29T06:11:30Z",
            alert_to_timestamp="2026-09-29T06:11:35Z",
            template_path=str(tmp_path),
            template_name=template_name,
            report_prompt="Ignore the template layout and write a short prose summary.\n{template}\n{agent_version}",
            llm=RunnableLambda(good_llm),
            image_url="http://example.com/snap.png",
            video_url="http://example.com/clip.mp4",
        )

        assert "##Resources" in content
        assert "http://example.com/snap.png" in content

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

    @pytest.mark.asyncio
    async def test_validation_failure_skips_markdown_and_pdf_save(self, tmp_path):
        template_name = "custom.md"
        (tmp_path / template_name).write_text("# Custom\n\n{notes}", encoding="utf-8")

        config = TemplateReportGenConfig(
            object_store="object_store",
            llm_name="llm",
            video_understanding_tool="video_understanding",
            picture_url_tool="vst_picture_url",
            video_url_tool="vst_video_url",
            template_path=str(tmp_path),
            template_name=template_name,
            report_prompt="Populate:\n{template}\n{agent_version}",
            vlm_prompts=["describe"],
            required_report_fields=["Detailed Description"],
        )

        object_store = AsyncMock()
        vlm_tool = SimpleNamespace(ainvoke=AsyncMock(return_value="visible person in aisle"))
        picture_tool = SimpleNamespace(
            ainvoke=AsyncMock(return_value=SimpleNamespace(image_url="http://example.com/snap.png", video_url=None))
        )
        video_tool = SimpleNamespace(
            ainvoke=AsyncMock(return_value=SimpleNamespace(video_url="http://example.com/clip.mp4"))
        )

        async def bad_llm(_prompt):
            return AIMessage(content=_sample_table_report(**{"Detailed Description": "{detailed_description}"}))

        builder = AsyncMock()
        builder.get_object_store_client = AsyncMock(return_value=object_store)
        builder.get_llm = AsyncMock(return_value=RunnableLambda(bad_llm))

        async def _get_tool(name, wrapper_type=None):
            tools = {
                "video_understanding": vlm_tool,
                "vst_picture_url": picture_tool,
                "vst_video_url": video_tool,
            }
            return tools[name]

        builder.get_tool = AsyncMock(side_effect=_get_tool)

        md_save = AsyncMock(return_value=("http://md", 1))
        pdf_save = AsyncMock(return_value=("http://pdf", 1))

        with (
            patch.object(template_report_gen_module, "_save_markdown_to_object_store", md_save),
            patch.object(template_report_gen_module, "_save_pdf_to_object_store", pdf_save),
        ):
            gen = template_report_gen.__wrapped__(config, builder)
            function_info = await gen.__anext__()
            with pytest.raises(
                ReportContentValidationError,
                match="unresolved_placeholder:Detailed Description",
            ):
                await function_info.single_fn(
                    TemplateReportGenInput(
                        alert_sensor_id="Camera_01",
                        alert_from_timestamp="2026-09-29T06:11:30Z",
                        alert_to_timestamp="2026-09-29T06:11:35Z",
                        alert_metadata={
                            "Id": "inc-123",
                            "category": "Person in forklift aisle",
                            "sensorId": "Camera_01",
                        },
                    )
                )

        md_save.assert_not_called()
        pdf_save.assert_not_called()
        object_store.upsert_object.assert_not_called()

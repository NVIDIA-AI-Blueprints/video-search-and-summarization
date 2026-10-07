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
"""Unit tests for report_agent module."""

from datetime import datetime
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import ValidationError
import pytest

from vss_agents.agents.data_models import AgentMessageChunkType
from vss_agents.agents.data_models import AgentOutput
from vss_agents.agents.report_agent import INCIDENT_REPORT_METADATA_FIELDS
from vss_agents.agents.report_agent import ReportAgentConfig
from vss_agents.agents.report_agent import ReportAgentInput
from vss_agents.agents.report_agent import VideoReportAgentInput
from vss_agents.agents.report_agent import _build_report_side_effects
from vss_agents.agents.report_agent import _format_hitl_prompts
from vss_agents.agents.report_agent import _format_multi_video_downloads
from vss_agents.agents.report_agent import _format_multi_video_media
from vss_agents.agents.report_agent import _format_sensor_display
from vss_agents.agents.report_agent import _format_single_video_downloads
from vss_agents.agents.report_agent import report_agent
from vss_agents.tools.template_report_gen import ReportContentValidationError
from vss_agents.tools.template_report_gen import TemplateReportGenOutput


class TestReportAgentInput:
    """Test ReportAgentInput model."""

    def test_defaults(self):
        input_data = ReportAgentInput()
        assert input_data.start_time is None
        assert input_data.end_time is None
        assert input_data.incident_id is None
        assert input_data.source is None
        assert input_data.source_type is None
        assert input_data.vlm_reasoning is None

    def test_with_incident_id(self):
        input_data = ReportAgentInput(incident_id="incident-123")
        assert input_data.incident_id == "incident-123"

    def test_with_time_range(self):
        start = datetime(2025, 1, 1, 0, 0)
        end = datetime(2025, 1, 1, 23, 59)
        input_data = ReportAgentInput(start_time=start, end_time=end)
        assert input_data.start_time == start
        assert input_data.end_time == end

    def test_with_source_sensor(self):
        input_data = ReportAgentInput(source="sensor-001", source_type="sensor")
        assert input_data.source == "sensor-001"
        assert input_data.source_type == "sensor"

    def test_with_source_place(self):
        input_data = ReportAgentInput(source="Main Street", source_type="place")
        assert input_data.source_type == "place"

    def test_invalid_source_type(self):
        with pytest.raises(ValidationError):
            ReportAgentInput(source="test", source_type="invalid")

    def test_vlm_reasoning_enabled(self):
        input_data = ReportAgentInput(vlm_reasoning=True)
        assert input_data.vlm_reasoning is True

    def test_vlm_reasoning_disabled(self):
        input_data = ReportAgentInput(vlm_reasoning=False)
        assert input_data.vlm_reasoning is False

    def test_report_fetches_authoritative_metadata(self):
        assert INCIDENT_REPORT_METADATA_FIELDS == ["category", "place", "objectIds", "info"]

    def test_report_content_validation_error_is_value_error(self):
        """Report agent VA-MCP path catches ValueError and returns status=error."""
        assert issubclass(ReportContentValidationError, ValueError)
        err = ReportContentValidationError("empty_or_whitespace_body", response_len=0, body_len=0)
        assert "empty_or_whitespace_body" in str(err)


class TestVideoReportAgentInput:
    """Test VideoReportAgentInput model."""

    def test_all_fields(self):
        input_data = VideoReportAgentInput(sensor_id="vst-sensor-001", user_query="What's happening in this video?")
        assert input_data.sensor_id == "vst-sensor-001"
        assert input_data.user_query == "What's happening in this video?"

    def test_missing_sensor_id(self):
        with pytest.raises(ValidationError):
            VideoReportAgentInput(user_query="test")

    def test_only_sensor_id(self):
        input_data = VideoReportAgentInput(sensor_id="vst-sensor-001")
        assert input_data.sensor_id == "vst-sensor-001"
        assert input_data.user_query == "Generate a detailed report of the video."


class TestBuildReportSideEffects:
    """Test the download/media side effects attached to a generated report."""

    @staticmethod
    def _result(**overrides):
        """A TemplateReportGenOutput-shaped result; every URL field is present."""
        fields = {
            "http_url": "http://vss:7777/static/agent_report_20260904_221530.md",
            "pdf_url": "http://vss:7777/static/agent_report_20260904_221530.pdf",
            "image_url": "",
            "video_url": None,
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    def test_both_urls_present_links_both(self):
        side_effects = _build_report_side_effects(self._result(), "incident-1")
        downloads = side_effects["report_downloads"]
        assert "**Report Downloads:**" in downloads
        assert "- [Markdown Report](http://vss:7777/static/agent_report_20260904_221530.md)" in downloads
        assert "- [PDF Report](http://vss:7777/static/agent_report_20260904_221530.pdf)" in downloads

    def test_pdf_empty_links_markdown_only(self):
        side_effects = _build_report_side_effects(self._result(pdf_url=""), "incident-1")
        downloads = side_effects["report_downloads"]
        assert "[Markdown Report]" in downloads
        assert "[PDF Report]" not in downloads
        # No empty anchor is emitted for the missing artifact.
        assert "()" not in downloads

    def test_markdown_empty_links_pdf_only(self):
        side_effects = _build_report_side_effects(self._result(http_url=""), "incident-1")
        downloads = side_effects["report_downloads"]
        assert "[PDF Report]" in downloads
        assert "[Markdown Report]" not in downloads
        assert "()" not in downloads

    def test_both_urls_empty_says_so_instead_of_a_bare_heading(self):
        """The regression: hasattr is always true, so only the values can gate this."""
        side_effects = _build_report_side_effects(self._result(http_url="", pdf_url=""), "incident-1")
        downloads = side_effects["report_downloads"]
        # Never a heading with no anchors under it.
        assert downloads.strip() != "**Report Downloads:**"
        assert "[Markdown Report]" not in downloads
        assert "[PDF Report]" not in downloads
        assert "nothing to download" in downloads

    def test_hasattr_is_vacuous_on_the_real_output_model(self):
        """Guards the premise of the fix against a future field-optionality change."""
        assert "http_url" in TemplateReportGenOutput.model_fields
        assert "pdf_url" in TemplateReportGenOutput.model_fields
        empty = TemplateReportGenOutput(
            http_url="",
            pdf_url="",
            object_store_key="k",
            summary="s",
            file_size=0,
            pdf_file_size=0,
            content="c",
            image_url="",
        )
        assert hasattr(empty, "http_url") and hasattr(empty, "pdf_url")
        assert "[Markdown Report]" not in _build_report_side_effects(empty, "incident-1")["report_downloads"]

    def test_media_omitted_when_absent(self):
        side_effects = _build_report_side_effects(self._result(), "incident-1")
        assert "media" not in side_effects

    def test_media_included_when_present(self):
        side_effects = _build_report_side_effects(
            self._result(image_url="http://vss:7777/snap.jpg", video_url="http://vss:7777/clip.mp4"),
            "incident-1",
        )
        media = side_effects["media"]
        assert "- ![Incident Snapshot](http://vss:7777/snap.jpg)" in media
        assert "- [Incident Video](http://vss:7777/clip.mp4)" in media


class TestReportAgentValidationFailureBoundary:
    """Validation failures must surface as status=error with no download side effects."""

    @pytest.mark.asyncio
    async def test_validation_failure_returns_error_without_downloads(self):
        config = ReportAgentConfig(
            get_incidents_tool="get_incidents",
            get_incident_tool="get_incident",
            template_report_tool="template_report_gen",
        )
        incident = {
            "Id": "inc-123",
            "sensorId": "Camera_01",
            "timestamp": "2026-09-29T06:11:30Z",
            "end": "2026-09-29T06:11:35Z",
            "category": "Person in forklift aisle",
        }
        get_incident_tool = SimpleNamespace(ainvoke=AsyncMock(return_value=json.dumps(incident)))
        template_report_tool = SimpleNamespace(
            ainvoke=AsyncMock(
                side_effect=ReportContentValidationError(
                    "missing_required_field:Detailed Description",
                    response_len=120,
                    body_len=120,
                )
            )
        )

        builder = AsyncMock()

        async def _get_tool(name, wrapper_type=None):
            tools = {
                "get_incidents": SimpleNamespace(ainvoke=AsyncMock()),
                "get_incident": get_incident_tool,
                "template_report_gen": template_report_tool,
            }
            return tools[str(name)]

        builder.get_tool = AsyncMock(side_effect=_get_tool)

        gen = report_agent.__wrapped__(config, builder)
        function_info = await gen.__anext__()
        chunks = [chunk async for chunk in function_info.stream_fn(ReportAgentInput(incident_id="inc-123"))]

        assert chunks
        assert chunks[-1].type == AgentMessageChunkType.FINAL
        output = AgentOutput.model_validate_json(chunks[-1].content)
        assert output.status == "error"
        assert "missing_required_field:Detailed Description" in (output.error_message or "")
        assert all("successfully" not in message.lower() for message in output.messages)
        assert not output.side_effects or "report_downloads" not in output.side_effects
        template_report_tool.ainvoke.assert_awaited_once()
        assert get_incident_tool.ainvoke.await_args.args[0]["includes"] == INCIDENT_REPORT_METADATA_FIELDS

    @pytest.mark.asyncio
    async def test_llm_provider_error_is_not_returned_or_logged(self, caplog):
        config = ReportAgentConfig(
            get_incidents_tool="get_incidents",
            get_incident_tool="get_incident",
            template_report_tool="template_report_gen",
        )
        incident = {
            "Id": "inc-123",
            "sensorId": "Camera_01",
            "timestamp": "2026-09-29T06:11:30Z",
            "end": "2026-09-29T06:11:35Z",
        }
        provider_secret = "provider-prompt-echo-7c1e"  # pragma: allowlist secret
        llm_failure = ValueError("Failed to generate custom report with LLM")
        llm_failure.__cause__ = RuntimeError(provider_secret)
        get_incident_tool = SimpleNamespace(ainvoke=AsyncMock(return_value=json.dumps(incident)))
        template_report_tool = SimpleNamespace(ainvoke=AsyncMock(side_effect=llm_failure))

        builder = AsyncMock()

        async def _get_tool(name, wrapper_type=None):
            tools = {
                "get_incidents": SimpleNamespace(ainvoke=AsyncMock()),
                "get_incident": get_incident_tool,
                "template_report_gen": template_report_tool,
            }
            return tools[str(name)]

        builder.get_tool = AsyncMock(side_effect=_get_tool)
        gen = report_agent.__wrapped__(config, builder)
        function_info = await gen.__anext__()
        caplog.set_level(logging.DEBUG)
        chunks = [chunk async for chunk in function_info.stream_fn(ReportAgentInput(incident_id="inc-123"))]

        assert chunks
        output = AgentOutput.model_validate_json(chunks[-1].content)
        rendered = chunks[-1].content + "\n" + "\n".join(output.messages) + "\n" + (output.error_message or "")
        assert output.status == "error"
        assert provider_secret not in rendered
        assert provider_secret not in caplog.text
        assert all("successfully" not in message.lower() for message in output.messages)
        assert not output.side_effects or "report_downloads" not in output.side_effects

    @pytest.mark.asyncio
    async def test_get_incidents_requests_authoritative_metadata_fields(self):
        config = ReportAgentConfig(
            get_incidents_tool="get_incidents",
            get_incident_tool="get_incident",
            template_report_tool="template_report_gen",
        )
        get_incidents_tool = SimpleNamespace(ainvoke=AsyncMock(return_value=json.dumps({"incidents": []})))
        builder = AsyncMock()

        async def _get_tool(name, wrapper_type=None):
            tools = {
                "get_incidents": get_incidents_tool,
                "get_incident": SimpleNamespace(ainvoke=AsyncMock()),
                "template_report_gen": SimpleNamespace(ainvoke=AsyncMock()),
            }
            return tools[str(name)]

        builder.get_tool = AsyncMock(side_effect=_get_tool)
        gen = report_agent.__wrapped__(config, builder)
        function_info = await gen.__anext__()
        chunks = [chunk async for chunk in function_info.stream_fn(ReportAgentInput())]

        assert chunks[-1].type == AgentMessageChunkType.FINAL
        assert get_incidents_tool.ainvoke.await_args.args[0]["includes"] == [
            "category",
            "place",
            "objectIds",
            "info",
        ]


def _incident_config() -> ReportAgentConfig:
    return ReportAgentConfig(
        get_incidents_tool="get_incidents",
        get_incident_tool="get_incident",
        template_report_tool="template_report_gen",
    )


def _builder_for(tools: dict[str, SimpleNamespace]) -> AsyncMock:
    builder = AsyncMock()

    async def _get_tool(name, wrapper_type=None):
        return tools[str(name)]

    builder.get_tool = AsyncMock(side_effect=_get_tool)
    return builder


async def _incident_chunks(tools: dict[str, SimpleNamespace], report_input: ReportAgentInput) -> list:
    gen = report_agent.__wrapped__(_incident_config(), _builder_for(tools))
    function_info = await gen.__anext__()
    return [chunk async for chunk in function_info.stream_fn(report_input)]


class TestIncidentRetrieval:
    """Incident lookup, verified-index retry, and report invocation stay in order."""

    @pytest.mark.asyncio
    async def test_missing_incident_retries_verified_index_then_reports(self):
        incident = {"id": "inc-123", "sensorId": "Camera_01", "timestamp": "t0", "end": "t1"}
        get_incident_tool = SimpleNamespace(ainvoke=AsyncMock(side_effect=[None, json.dumps(incident)]))
        template_report_tool = SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=SimpleNamespace(http_url="http://vss/report.md", pdf_url="", image_url="", video_url="")
            )
        )
        chunks = await _incident_chunks(
            {
                "get_incidents": SimpleNamespace(ainvoke=AsyncMock()),
                "get_incident": get_incident_tool,
                "template_report_gen": template_report_tool,
            },
            ReportAgentInput(incident_id="inc-123"),
        )

        assert [chunk.type for chunk in chunks] == [
            AgentMessageChunkType.TOOL_CALL,
            AgentMessageChunkType.TOOL_CALL,
            AgentMessageChunkType.TOOL_CALL,
            AgentMessageChunkType.FINAL,
        ]
        assert "vlm_verified': True" in chunks[1].content
        assert "template_report_gen" in chunks[2].content
        output = AgentOutput.model_validate_json(chunks[-1].content)
        assert output.status == "success"
        assert output.metadata["incident_id"] == "inc-123"
        assert "report_downloads" in output.side_effects
        assert get_incident_tool.ainvoke.await_args_list[1].args[0]["vlm_verified"] is True

    @pytest.mark.asyncio
    async def test_invalid_incidents_payload_returns_error_without_a_report(self):
        get_incidents_tool = SimpleNamespace(ainvoke=AsyncMock(return_value="not-json"))
        template_report_tool = SimpleNamespace(ainvoke=AsyncMock())
        chunks = await _incident_chunks(
            {
                "get_incidents": get_incidents_tool,
                "get_incident": SimpleNamespace(ainvoke=AsyncMock()),
                "template_report_gen": template_report_tool,
            },
            ReportAgentInput(),
        )

        output = AgentOutput.model_validate_json(chunks[-1].content)
        assert output.status == "error"
        assert "invalid response" in output.messages[0]
        template_report_tool.ainvoke.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tuple_incidents_result_uses_the_first_incident(self):
        incident = {"Id": "inc-9", "sensorId": "Camera_02"}
        get_incidents_tool = SimpleNamespace(ainvoke=AsyncMock(return_value=([incident], None)))
        template_report_tool = SimpleNamespace(
            ainvoke=AsyncMock(return_value=SimpleNamespace(http_url="http://vss/report.md", pdf_url=""))
        )
        chunks = await _incident_chunks(
            {
                "get_incidents": get_incidents_tool,
                "get_incident": SimpleNamespace(ainvoke=AsyncMock()),
                "template_report_gen": template_report_tool,
            },
            ReportAgentInput(source="Camera_02", source_type="sensor"),
        )

        output = AgentOutput.model_validate_json(chunks[-1].content)
        assert output.status == "success"
        assert template_report_tool.ainvoke.await_args.args[0]["incident_id"] == "inc-9"
        assert template_report_tool.ainvoke.await_args.args[0]["alert_metadata"] == incident


class TestVideoReportFormatting:
    """Uploaded-video, multi-video, RTSP, cancellation, and HITL output stay stable."""

    def test_sensor_display_and_download_layouts(self):
        assert _format_sensor_display("cam-a") == "cam-a"
        assert _format_sensor_display(["cam-a"]) == "cam-a"
        assert _format_sensor_display(["cam-a", "cam-b"]) == "cam-a & cam-b"
        assert _format_sensor_display(["cam-a", "cam-b", "cam-c"]) == "cam-a, cam-b & cam-c"
        downloads = _format_single_video_downloads(
            SimpleNamespace(http_url="http://vss/a.md", pdf_url="http://vss/a.pdf")
        )
        assert "- [Markdown Report](http://vss/a.md)" in downloads
        assert "- [PDF Report](http://vss/a.pdf)" in downloads
        multi = _format_multi_video_downloads(
            [
                {"sensor_id": "cam-a", "http_url": "http://vss/a.md", "pdf_url": ""},
                {"sensor_id": "cam-b", "http_url": "http://vss/b.md", "pdf_url": "http://vss/b.pdf"},
            ]
        )
        assert "**cam-a:**" in multi
        assert "[PDF Report](http://vss/b.pdf)" in multi
        media = _format_multi_video_media(
            [{"sensor_id": "cam-a", "video_url": ""}, {"sensor_id": "cam-b", "video_url": "http://vss/b.mp4"}]
        )
        assert media is not None
        assert "**cam-b:**" in media
        assert "[Video Playback](http://vss/b.mp4)" in media
        assert _format_multi_video_media([{"sensor_id": "cam-a"}]) is None

    def test_hitl_prompt_formatting_skips_an_empty_header(self):
        assert _format_hitl_prompts(None) is None
        assert _format_hitl_prompts({}) is None
        text = _format_hitl_prompts(
            {"scenario": "loading dock", "events": ["entry"], "objects_of_interest": ["pallet", "person"]}
        )
        assert text is not None
        assert "- Scenario: loading dock" in text
        assert "- Events of interest: entry" in text
        assert "- Objects of interest: pallet, person" in text

    @pytest.mark.asyncio
    async def test_multi_video_success_includes_downloads_hitl_and_sensor_display(self):
        report = SimpleNamespace(
            http_url="http://vss/combined.md",
            pdf_url="http://vss/combined.pdf",
            video_url="http://vss/combined.mp4",
            summary="Two clips reviewed.",
            file_size=11,
            pdf_file_size=22,
            lvs_fallback_warning="Fell back to per-clip analysis.",
            hitl_prompts={"scenario": "aisle"},
            all_reports=[
                {"sensor_id": "cam-a", "http_url": "http://vss/a.md", "pdf_url": "", "video_url": "http://vss/a.mp4"},
                {"sensor_id": "cam-b", "http_url": "http://vss/b.md", "pdf_url": "http://vss/b.pdf", "video_url": ""},
            ],
        )
        video_tool = SimpleNamespace(ainvoke=AsyncMock(return_value=report))
        gen = report_agent.__wrapped__(
            ReportAgentConfig(video_report_tool="video_report_gen"),
            _builder_for({"video_report_gen": video_tool}),
        )
        function_info = await gen.__anext__()
        chunks = [
            chunk
            async for chunk in function_info.stream_fn(
                VideoReportAgentInput(sensor_id=["cam-a", "cam-b", "cam-c"], user_query="What happened?")
            )
        ]

        output = AgentOutput.model_validate_json(chunks[-1].content)
        assert output.status == "success"
        assert "cam-a, cam-b & cam-c" in output.messages[0]
        assert output.messages[2] == "Two clips reviewed."
        assert "**cam-a:**" in output.side_effects["report_downloads"]
        assert "[Video Playback](http://vss/a.mp4)" in output.side_effects["media"]
        assert "aisle" in output.side_effects["hitl_prompts"]
        assert output.side_effects["lvs_fallback_warning"] == "Fell back to per-clip analysis."
        assert output.side_effects["artifact_note"]
        assert video_tool.ainvoke.await_args.args[0]["sensor_id"] == ["cam-a", "cam-b", "cam-c"]

    @pytest.mark.asyncio
    async def test_cancelled_rtsp_report_has_no_download_links(self):
        video_tool = SimpleNamespace(
            ainvoke=AsyncMock(return_value=SimpleNamespace(http_url="", summary="Waiting for captions."))
        )
        gen = report_agent.__wrapped__(
            ReportAgentConfig(video_report_tool="video_report_gen"),
            _builder_for({"video_report_gen": video_tool}),
        )
        function_info = await gen.__anext__()
        chunks = [
            chunk
            async for chunk in function_info.stream_fn(
                VideoReportAgentInput(sensor_id="door-cam", media_type="rtsp", start_time=0, end_time=0)
            )
        ]

        output = AgentOutput.model_validate_json(chunks[-1].content)
        assert output.status == "success"
        assert output.messages == ["Waiting for captions."]
        assert output.side_effects == {}
        assert video_tool.ainvoke.await_args.args[0]["media_type"] == "rtsp"
        assert video_tool.ainvoke.await_args.args[0]["end_time"] == 0

    @pytest.mark.asyncio
    async def test_websocket_prompt_error_uses_the_connection_message(self):
        video_tool = SimpleNamespace(
            ainvoke=AsyncMock(side_effect=RuntimeError("No human prompt callback was registered for this session"))
        )
        gen = report_agent.__wrapped__(
            ReportAgentConfig(video_report_tool="video_report_gen"),
            _builder_for({"video_report_gen": video_tool}),
        )
        function_info = await gen.__anext__()
        chunks = [chunk async for chunk in function_info.stream_fn(VideoReportAgentInput(sensor_id="cam-a"))]

        output = AgentOutput.model_validate_json(chunks[-1].content)
        assert output.status == "error"
        assert "websocket" in output.messages[0]
        assert "report_downloads" not in (output.side_effects or {})

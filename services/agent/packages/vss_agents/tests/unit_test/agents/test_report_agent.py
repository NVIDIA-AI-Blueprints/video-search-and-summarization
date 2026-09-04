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
from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from vss_agents.agents.report_agent import ReportAgentInput
from vss_agents.agents.report_agent import VideoReportAgentInput
from vss_agents.agents.report_agent import _build_report_side_effects
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

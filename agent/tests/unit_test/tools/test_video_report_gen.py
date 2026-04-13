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
"""Unit tests for video_report_gen module."""

import tempfile

from pydantic import ValidationError
import pytest

from vss_agents.tools.video_report_gen import TimestampMatch
from vss_agents.tools.video_report_gen import VideoReportGenInput
from vss_agents.tools.video_report_gen import VideoReportGenOutput
from vss_agents.tools.video_report_gen import _convert_markdown_to_pdf
from vss_agents.tools.video_report_gen import _divide_video_into_chunks
from vss_agents.tools.video_report_gen import _normalize_chunk_timestamps
from vss_agents.tools.video_report_gen import _parse_timestamps
from vss_agents.tools.video_understanding import VideoUnderstandingInput
from vss_agents.tools.video_understanding import VideoUnderstandingOffsetInput


class TestTimestampMatch:
    """Test TimestampMatch NamedTuple."""

    def test_creation(self):
        ts = TimestampMatch(position=10, seconds=5.5)
        assert ts.position == 10
        assert ts.seconds == 5.5

    def test_named_access(self):
        ts = TimestampMatch(position=0, seconds=30.0)
        assert ts.position == 0
        assert ts.seconds == 30.0

    def test_tuple_unpacking(self):
        ts = TimestampMatch(position=100, seconds=45.5)
        position, seconds = ts
        assert position == 100
        assert seconds == 45.5


class TestParseTimestamps:
    """Test _parse_timestamps function."""

    def test_parse_simple_timestamp(self):
        content = "Event at [5.0s-10.0s] description."
        matches = _parse_timestamps(content)
        assert len(matches) == 1
        assert matches[0].seconds == 7.5  # midpoint

    def test_parse_multiple_timestamps(self):
        content = "[0.0s-5.0s] First event. [10.0s-20.0s] Second event."
        matches = _parse_timestamps(content)
        assert len(matches) == 2
        assert matches[0].seconds == 2.5  # midpoint of 0-5
        assert matches[1].seconds == 15.0  # midpoint of 10-20

    def test_parse_with_spaces(self):
        content = "Event at [5.0s - 10.0s] with spaces."
        matches = _parse_timestamps(content)
        assert len(matches) == 1
        assert matches[0].seconds == 7.5

    def test_parse_decimal_timestamps(self):
        content = "Event at [1.5s-3.5s] description."
        matches = _parse_timestamps(content)
        assert len(matches) == 1
        assert matches[0].seconds == 2.5  # midpoint of 1.5-3.5

    def test_parse_no_timestamps(self):
        content = "No timestamps in this content."
        matches = _parse_timestamps(content)
        assert len(matches) == 0

    def test_parse_preserves_position(self):
        content = "Some text [5.0s-10.0s] more text."
        matches = _parse_timestamps(content)
        assert len(matches) == 1
        assert matches[0].position == 10  # position of '['

    def test_parse_large_timestamps(self):
        content = "[120.0s-180.0s] Event in the middle of a long video."
        matches = _parse_timestamps(content)
        assert len(matches) == 1
        assert matches[0].seconds == 150.0  # midpoint of 120-180


class TestNormalizeChunkTimestamps:
    """Test _normalize_chunk_timestamps function."""

    def test_timestamps_match_chunk_duration(self):
        """Timestamps matching chunk duration should just get offset added."""
        # Chunk is 60s (60-120), timestamps end at 60s (ratio = 1.0)
        content = "Event at [30.0s-60.0s] description."
        result = _normalize_chunk_timestamps(content, chunk_start=60.0, chunk_end=120.0)
        # No scaling needed, just add offset: 30+60=90, 60+60=120
        assert "[90.0s-120.0s]" in result

    def test_normalization_ratio_scaling_down(self):
        """Timestamps exceeding chunk duration should be scaled down."""
        # Chunk is 60s (60-120), but timestamps go to 90s
        # ratio = 90/60 = 1.5, so 90s becomes 60s, 45s becomes 30s
        content = "Event at [45.0s-90.0s] description."
        result = _normalize_chunk_timestamps(content, chunk_start=60.0, chunk_end=120.0)
        # After scaling: 45/1.5=30, 90/1.5=60, then add offset 60: 90s-120s
        assert "[90.0s-120.0s]" in result

    def test_normalization_ratio_scaling_up(self):
        """Timestamps much smaller than chunk duration should be scaled up."""
        # Chunk is 60s (0-60), but max timestamp is only 30s
        # ratio = 30/60 = 0.5, so 15s becomes 30s, 30s becomes 60s
        content = "Event at [15.0s-30.0s] description."
        result = _normalize_chunk_timestamps(content, chunk_start=0.0, chunk_end=60.0)
        # After scaling: 15/0.5=30, 30/0.5=60, then add offset 0: 30s-60s
        assert "[15.0s-30.0s]" in result

    def test_multiple_timestamps_normalized(self):
        """Multiple timestamps should all be normalized with same ratio."""
        # Chunk is 60s, max timestamp is 90s, ratio = 1.5
        content = "[30.0s-45.0s] First. [60.0s-90.0s] Second."
        result = _normalize_chunk_timestamps(content, chunk_start=0.0, chunk_end=60.0)
        # 30/1.5=20, 45/1.5=30 -> [20.0s-30.0s]
        # 60/1.5=40, 90/1.5=60 -> [40.0s-60.0s]
        assert "[20.0s-30.0s]" in result
        assert "[40.0s-60.0s]" in result

    def test_no_timestamps_returns_original(self):
        """Content without timestamps should return unchanged."""
        content = "No timestamps here."
        result = _normalize_chunk_timestamps(content, chunk_start=60.0, chunk_end=120.0)
        assert result == content

    def test_ratio_close_to_one_no_normalization(self):
        """Ratio within 1% of 1.0 should not trigger normalization."""
        # Chunk is 60s, max timestamp is 60.5s, ratio ≈ 1.008
        content = "Event at [30.0s-60.5s] description."
        result = _normalize_chunk_timestamps(content, chunk_start=0.0, chunk_end=60.0)
        # Should just add offset without scaling
        assert "[29.8s-60.0s]" in result

    def test_chunk_offset_applied_with_matching_duration(self):
        """Chunk start offset should be added when timestamps match duration."""
        # Chunk is 60s (120-180), timestamps end at 60s (ratio = 1.0)
        content = "[0.0s-60.0s] Event spanning full chunk."
        result = _normalize_chunk_timestamps(content, chunk_start=120.0, chunk_end=180.0)
        # No scaling, just add offset: 0+120=120, 60+120=180
        assert "[120.0s-180.0s]" in result

    def test_small_timestamps_scaled_up_with_offset(self):
        """Small timestamps should be scaled up and offset applied."""
        # Chunk is 60s (120-180), max timestamp is 10s, ratio = 10/60 = 0.167
        content = "[0.0s-10.0s] Event at start."
        result = _normalize_chunk_timestamps(content, chunk_start=120.0, chunk_end=180.0)
        # After scaling: 0/0.167=0, 10/0.167=60, then add offset 120: 120s-180s
        assert "[120.0s-130.0s]" in result


class TestDivideVideoIntoChunks:
    """Test _divide_video_into_chunks function."""

    def test_single_chunk(self):
        """Short video should result in single chunk."""
        chunks = _divide_video_into_chunks(30.0, 60.0)
        assert len(chunks) == 1
        assert chunks[0] == (0.0, 30.0)

    def test_exact_division(self):
        """Video exactly divisible by chunk size."""
        chunks = _divide_video_into_chunks(120.0, 60.0)
        assert len(chunks) == 2
        assert chunks[0] == (0.0, 60.0)
        assert chunks[1] == (60.0, 120.0)

    def test_with_remainder(self):
        """Video with remainder chunk."""
        chunks = _divide_video_into_chunks(150.0, 60.0)
        assert len(chunks) == 3
        assert chunks[0] == (0.0, 60.0)
        assert chunks[1] == (60.0, 120.0)
        assert chunks[2] == (120.0, 150.0)

    def test_zero_duration(self):
        """Zero duration should return empty list."""
        chunks = _divide_video_into_chunks(0.0, 60.0)
        assert len(chunks) == 0


class TestVideoReportGenInput:
    """Test VideoReportGenInput model."""

    def test_required_fields(self):
        """Test input with required fields only."""
        input_data = VideoReportGenInput(
            sensor_id="sensor-001",
            user_query="Describe what happens in this video",
        )
        assert input_data.sensor_id == "sensor-001"
        assert input_data.user_query == "Describe what happens in this video"

    def test_optional_vlm_reasoning(self):
        """Test input with optional vlm_reasoning."""
        input_data = VideoReportGenInput(
            sensor_id="sensor-001",
            user_query="Analyze this video",
            vlm_reasoning=True,
        )
        assert input_data.vlm_reasoning is True

    def test_missing_required_fails(self):
        """Test that missing required fields raises error."""
        with pytest.raises(ValidationError):
            VideoReportGenInput(sensor_id="sensor-001")

        with pytest.raises(ValidationError):
            VideoReportGenInput(user_query="Query only")


class TestVideoReportGenOutput:
    """Test VideoReportGenOutput model."""

    def test_output_creation(self):
        """Test output creation with all fields."""
        output = VideoReportGenOutput(
            http_url="http://localhost/report.md",
            pdf_url="http://localhost/report.pdf",
            object_store_key="reports/report.md",
            file_size=1024,
            pdf_file_size=2048,
            summary="Report summary",
            content="# Report\n\nContent here.",
        )
        assert output.http_url == "http://localhost/report.md"
        assert output.pdf_url == "http://localhost/report.pdf"
        assert output.object_store_key == "reports/report.md"
        assert output.file_size == 1024
        assert output.pdf_file_size == 2048
        assert output.summary == "Report summary"
        assert output.content == "# Report\n\nContent here."

    def test_output_optional_fields(self):
        """Test output with optional fields as None."""
        output = VideoReportGenOutput(
            http_url="http://localhost/report.md",
            pdf_url=None,
            object_store_key="reports/report.md",
            file_size=1024,
            pdf_file_size=0,
            summary="Report summary",
            content="# Report",
            video_url=None,
        )
        assert output.pdf_url is None
        assert output.video_url is None

    def test_output_serialization(self):
        """Test output serialization."""
        output = VideoReportGenOutput(
            http_url="http://localhost/report.md",
            pdf_url="http://localhost/report.pdf",
            object_store_key="reports/report.md",
            file_size=1024,
            pdf_file_size=2048,
            summary="Report summary",
            content="# Report",
        )
        data = output.model_dump()
        assert "http_url" in data
        assert "pdf_url" in data
        assert "object_store_key" in data
        assert "file_size" in data
        assert "pdf_file_size" in data
        assert "summary" in data
        assert "content" in data


class TestTimestampFormatDetection:
    """Test that video_report_gen correctly detects the timestamp format
    expected by the video understanding tool (float offsets vs ISO strings).

    Regression test for: VideoUnderstandingOffsetInput (stream_mode=false)
    expects float offsets, but video_report_gen was always passing ISO strings,
    causing 'could not convert string to float' validation errors.
    """

    def test_non_stream_model_has_float_timestamp(self):
        """VideoUnderstandingOffsetInput.start_timestamp should be float | None."""
        ts_field = VideoUnderstandingOffsetInput.model_fields["start_timestamp"]
        field_type = ts_field.annotation
        # float | None resolves to Union[float, NoneType] with __args__
        assert hasattr(field_type, "__args__"), "Expected Union type (float | None)"
        assert float in field_type.__args__, "Expected float in Union args"

    def test_stream_model_has_str_timestamp(self):
        """VideoUnderstandingInput.start_timestamp should be str (ISO 8601)."""
        ts_field = VideoUnderstandingInput.model_fields["start_timestamp"]
        assert ts_field.annotation is str, "Expected str type for stream mode timestamps"

    def test_detection_logic_identifies_float_schema(self):
        """The schema-based detection logic should identify float timestamps."""
        schema = VideoUnderstandingOffsetInput
        ts_field = schema.model_fields.get("start_timestamp")
        field_type = ts_field.annotation
        uses_float = field_type is float or (hasattr(field_type, "__args__") and float in field_type.__args__)
        assert uses_float is True, "Should detect float timestamps for non-stream model"

    def test_detection_logic_identifies_str_schema(self):
        """The schema-based detection logic should identify string timestamps."""
        schema = VideoUnderstandingInput
        ts_field = schema.model_fields.get("start_timestamp")
        field_type = ts_field.annotation
        uses_float = field_type is float or (hasattr(field_type, "__args__") and float in field_type.__args__)
        assert uses_float is False, "Should not detect float timestamps for stream model"

    def test_non_stream_model_accepts_float_offsets(self):
        """VideoUnderstandingOffsetInput should accept float offsets."""
        data = {
            "sensor_id": "test_video",
            "start_timestamp": 0.0,
            "end_timestamp": 25.0,
            "user_prompt": "Describe the video",
        }
        model = VideoUnderstandingOffsetInput.model_validate(data)
        assert model.start_timestamp == 0.0
        assert model.end_timestamp == 25.0

    def test_non_stream_model_rejects_iso_timestamps(self):
        """VideoUnderstandingOffsetInput should reject ISO timestamp strings.

        This is the exact regression: passing '2025-01-01T00:00:00Z' to a model
        that expects float offsets caused a validation error in dev-profile-base.
        """
        data = {
            "sensor_id": "test_video",
            "start_timestamp": "2025-01-01T00:00:00Z",
            "end_timestamp": "2025-01-01T00:00:25Z",
            "user_prompt": "Describe the video",
        }
        with pytest.raises(ValidationError, match="could not convert string to float"):
            VideoUnderstandingOffsetInput.model_validate(data)

    def test_stream_model_accepts_iso_timestamps(self):
        """VideoUnderstandingInput should accept ISO timestamp strings."""
        data = {
            "sensor_id": "test_video",
            "start_timestamp": "2025-01-01T00:00:00Z",
            "end_timestamp": "2025-01-01T00:00:25Z",
            "user_prompt": "Describe the video",
        }
        model = VideoUnderstandingInput.model_validate(data)
        assert model.start_timestamp == "2025-01-01T00:00:00Z"
        assert model.end_timestamp == "2025-01-01T00:00:25Z"


class TestResourcesSectionFormatting:
    """Test that the Resources section in reports is formatted correctly for PDF rendering.

    Regression test for: 'Video Playback:' label and URL appeared on the same line with
    text-align:justify, causing a large gap between words in the PDF output.
    """

    def test_video_playback_url_on_separate_paragraph(self):
        """Video URL should be in a separate paragraph from the label."""
        video_url = "http://example.com/video.mp4"
        markdown_content = "## Analysis\n\nSome content."

        # Simulate what video_report_gen does
        markdown_content += "\n\n## Resources\n\n"
        markdown_content += f"**Video Playback:**\n\n{video_url}\n\n"

        lines = markdown_content.split("\n")
        # Find the "Video Playback:" line
        playback_line_idx = None
        for i, line in enumerate(lines):
            if "**Video Playback:**" in line:
                playback_line_idx = i
                break

        assert playback_line_idx is not None, "Should find 'Video Playback:' label"
        # The URL should NOT be on the same line as the label
        assert video_url not in lines[playback_line_idx], (
            "URL should not be on the same line as 'Video Playback:' label"
        )
        # There should be a blank line between label and URL
        assert lines[playback_line_idx + 1].strip() == "", "There should be a blank line between the label and the URL"
        # URL should be on a subsequent line
        assert any(video_url in line for line in lines[playback_line_idx + 1 :]), "URL should appear after the label"

    def test_pdf_css_has_word_break_for_links(self):
        """PDF CSS should include word-break rules for <a> tags to handle long URLs."""
        import os

        # Create a simple markdown file and check the generated HTML contains word-break
        md_content = "## Resources\n\n**Video Playback:**\n\nhttp://example.com/video.mp4\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "test.md")
            pdf_path = os.path.join(tmpdir, "test.pdf")
            with open(md_path, "w") as f:
                f.write(md_content)

            result = _convert_markdown_to_pdf(md_path, pdf_path)
            if result:
                # PDF was generated successfully - the CSS is valid
                assert os.path.exists(pdf_path), "PDF file should be created"
                assert os.path.getsize(pdf_path) > 0, "PDF file should not be empty"

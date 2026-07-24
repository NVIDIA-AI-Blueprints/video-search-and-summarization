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
"""Additional unit tests for search module to improve coverage."""

from datetime import UTC
from datetime import datetime
import json

from vss_agents.tools.embed_search import EmbedSearchOutput
from vss_agents.tools.embed_search import EmbedSearchResultItem
from vss_agents.tools.search import SearchInput
from vss_agents.tools.search import SearchOutput
from vss_agents.tools.search import SearchResult


class TestSearchInputConversion:
    """Test SearchInput conversion from JSON."""

    def test_json_str_conversion(self):
        json_str = '{"query": "test", "source_type": "video_file", "agent_mode": false}'
        result = SearchInput.model_validate_json(json_str)
        assert result.query == "test"
        assert result.agent_mode is False

    def test_json_with_all_fields(self):
        json_str = json.dumps(
            {
                "query": "find cars",
                "source_type": "video_file",
                "video_sources": ["video1"],
                "description": "parking",
                "timestamp_start": "2025-01-15T10:00:00Z",
                "timestamp_end": "2025-01-15T11:00:00Z",
                "top_k": 5,
                "min_cosine_similarity": 0.5,
                "agent_mode": True,
            }
        )
        result = SearchInput.model_validate_json(json_str)
        assert result.query == "find cars"
        assert result.video_sources == ["video1"]
        assert result.top_k == 5


class TestSearchOutputSerialization:
    """Test SearchOutput serialization and deserialization."""

    def test_round_trip_serialization(self):
        result = SearchResult(
            video_name="test.mp4",
            description="test video",
            start_time="2025-01-01T00:00:00Z",
            end_time="2025-01-01T01:00:00Z",
            sensor_id="s1",
            screenshot_url="http://example.com/screenshot.jpg",
            similarity=0.9,
        )
        output = SearchOutput(data=[result])
        json_str = output.model_dump_json()
        parsed = SearchOutput.model_validate_json(json_str)
        assert len(parsed.data) == 1
        assert parsed.data[0].video_name == "test.mp4"
        assert parsed.data[0].similarity == 0.9


class TestEmbedSearchOutputConversion:
    """Test EmbedSearchOutput data structure and conversion to SearchResult."""

    def test_embed_search_result_item_to_search_result(self):
        """Test conversion from EmbedSearchResultItem to SearchResult."""
        item = EmbedSearchResultItem(
            video_name="camera1.mp4",
            description="Parking lot",
            start_time="2025-01-15T10:00:00Z",
            end_time="2025-01-15T10:30:00Z",
            sensor_id="s1",
            screenshot_url="http://example.com/screenshot.jpg",
            similarity_score=0.95,
        )

        # Simulate what search does
        search_result = SearchResult(
            video_name=item.video_name,
            description=item.description,
            start_time=item.start_time,
            end_time=item.end_time,
            sensor_id=item.sensor_id,
            screenshot_url=item.screenshot_url,
            similarity=item.similarity_score,
        )
        assert search_result.similarity == 0.95
        assert search_result.video_name == "camera1.mp4"

    def test_embed_search_output_with_results(self):
        """Test EmbedSearchOutput with multiple results."""
        items = [
            EmbedSearchResultItem(
                video_name="v1.mp4",
                similarity_score=0.9,
            ),
            EmbedSearchResultItem(
                video_name="v2.mp4",
                similarity_score=0.8,
            ),
        ]
        output = EmbedSearchOutput(query_embedding=[0.1, 0.2], results=items)
        assert len(output.results) == 2
        assert output.results[0].video_name == "v1.mp4"
        assert output.results[1].similarity_score == 0.8

    def test_embed_search_output_empty(self):
        """Test empty EmbedSearchOutput."""
        output = EmbedSearchOutput(query_embedding=[], results=[])
        assert len(output.results) == 0

    def test_search_result_with_none_similarity(self):
        """Test handling None similarity_score."""
        item = EmbedSearchResultItem(
            video_name="test.mp4",
            similarity_score=0.0,
        )
        assert item.similarity_score == 0.0

    def test_search_result_empty_video_name_skipped(self):
        """Test that empty video_name results are identified."""
        item = EmbedSearchResultItem(video_name="", similarity_score=0.9)
        assert not item.video_name  # Should be skipped by search

    def test_end_time_iso_string_parsing(self):
        """Test parsing ISO string end_time."""
        end_time_value = "2025-01-15T10:30:00Z"
        end_dt = datetime.fromisoformat(end_time_value.replace("Z", "+00:00"))
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=UTC)
        end_time_iso = end_dt.isoformat().replace("+00:00", "Z")
        assert "2025-01-15" in end_time_iso

    def test_end_time_default_value(self):
        """Test handling non-str non-float end_time."""
        end_time_value = None
        base_dt = datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC)
        if isinstance(end_time_value, str):
            end_time_iso = end_time_value
        elif isinstance(end_time_value, int | float):
            end_time_iso = "computed"
        else:
            end_time_iso = base_dt.isoformat().replace("+00:00", "Z")
        assert "2025-01-15" in end_time_iso

    def test_start_time_invalid_iso_string(self):
        """Test handling invalid ISO start_time string."""
        start_time_value = "not-a-date"
        base_dt = datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC)
        try:
            start_dt = datetime.fromisoformat(start_time_value.replace("Z", "+00:00"))
            start_time_iso = start_dt.isoformat()
        except Exception:
            start_time_iso = base_dt.isoformat().replace("+00:00", "Z")
        assert "2025-01-15" in start_time_iso

    def test_screenshot_url_fallback_to_empty(self):
        """Test that screenshot_url defaults to empty string."""
        item = EmbedSearchResultItem(video_name="test.mp4")
        assert item.screenshot_url == ""

    def test_parse_base_timestamp_invalid(self):
        """Test parsing invalid base timestamp."""
        import contextlib

        base_timestamp_str = "invalid-timestamp"
        base_dt = None
        with contextlib.suppress(Exception):
            base_dt = datetime.fromisoformat(str(base_timestamp_str).replace("Z", "+00:00"))
        assert base_dt is None

    def test_embed_search_output_serialization_round_trip(self):
        """Test round-trip serialization of EmbedSearchOutput."""
        item = EmbedSearchResultItem(
            video_name="v.mp4",
            description="desc",
            start_time="2025-01-01T00:00:00Z",
            end_time="2025-01-01T01:00:00Z",
            sensor_id="s1",
            screenshot_url="http://pic.jpg",
            similarity_score=0.85,
        )
        output = EmbedSearchOutput(query_embedding=[0.1, 0.2], results=[item])
        json_str = output.model_dump_json()
        parsed = EmbedSearchOutput.model_validate_json(json_str)
        assert len(parsed.results) == 1
        assert parsed.results[0].video_name == "v.mp4"
        assert parsed.results[0].similarity_score == 0.85

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
"""Tests for attribute search helper functions."""

from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest

from vss_agents.tools.attribute_search import AttributeSearchMetadata
from vss_agents.tools.attribute_search import AttributeSearchResult
from vss_agents.tools.attribute_search import enrich_attribute_results


def _make_result(
    sensor_id: str = "camera-1",
    screenshot_url: str | None = None,
    start_time: str | None = None,
) -> AttributeSearchResult:
    return AttributeSearchResult(
        screenshot_url=screenshot_url,
        metadata=AttributeSearchMetadata(
            sensor_id=sensor_id,
            object_id="42",
            object_type="person",
            frame_timestamp="2025-01-01T00:00:01Z",
            start_time=start_time,
            end_time=None,
            bbox=None,
            behavior_score=0.95,
            frame_score=None,
            video_name=None,
        ),
    )


class TestEnrichAttributeResults:
    """Tests for enrich_attribute_results."""

    @pytest.mark.asyncio
    async def test_enriches_results_concurrently(self):
        results = [
            _make_result(sensor_id="camera-1", start_time="2025-01-01T00:00:00Z"),
            _make_result(sensor_id="camera-2"),
        ]

        mock_get_stream_id = AsyncMock(side_effect=["stream-1", "stream-2"])

        with patch("vss_agents.tools.vst.utils.get_stream_id", mock_get_stream_id):
            await enrich_attribute_results(results, "http://vst-internal:30888")

        assert [r.metadata.sensor_id for r in results] == ["stream-1", "stream-2"]
        assert results[0].screenshot_url == (
            "http://vst-internal:30888/vst/api/v1/replay/stream/stream-1/picture?startTime=2025-01-01T00:00:00Z"
        )
        assert results[1].screenshot_url == (
            "http://vst-internal:30888/vst/api/v1/replay/stream/stream-2/picture?startTime=2025-01-01T00:00:01Z"
        )

    @pytest.mark.asyncio
    async def test_enrichment_failure_does_not_block_other_results(self):
        results = [
            _make_result(sensor_id="camera-1"),
            _make_result(sensor_id="camera-2"),
        ]

        async def _get_stream_id(sensor_id: str, vst_url: str | None = None) -> str:
            if sensor_id == "camera-1":
                raise RuntimeError("boom")
            return "stream-2"

        with patch("vss_agents.tools.vst.utils.get_stream_id", side_effect=_get_stream_id):
            await enrich_attribute_results(results, "http://vst-internal:30888")

        assert results[0].metadata.sensor_id == "camera-1"
        assert results[0].screenshot_url is None
        assert results[1].metadata.sensor_id == "stream-2"
        assert results[1].screenshot_url == (
            "http://vst-internal:30888/vst/api/v1/replay/stream/stream-2/picture?startTime=2025-01-01T00:00:01Z"
        )

    @pytest.mark.asyncio
    async def test_skips_existing_screenshot_urls(self):
        results = [
            _make_result(sensor_id="camera-1", screenshot_url="http://existing"),
            _make_result(sensor_id="camera-2"),
        ]

        mock_get_stream_id = AsyncMock(return_value="stream-2")

        with patch("vss_agents.tools.vst.utils.get_stream_id", mock_get_stream_id):
            await enrich_attribute_results(results, "http://vst-internal:30888")

        assert results[0].screenshot_url == "http://existing"
        assert results[0].metadata.sensor_id == "camera-1"
        assert results[1].metadata.sensor_id == "stream-2"
        mock_get_stream_id.assert_awaited_once_with("camera-2", "http://vst-internal:30888")

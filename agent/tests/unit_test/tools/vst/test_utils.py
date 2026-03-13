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
"""Unit tests for VST utils module."""

import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from vss_agents.tools.vst.timeline import get_timeline
from vss_agents.tools.vst.utils import VSTError
from vss_agents.tools.vst.utils import get_name_to_stream_id_map
from vss_agents.tools.vst.utils import validate_video_url

# Sample mock data based on real VST server responses
MOCK_STREAMS_RESPONSE = [
    {
        "24c5a7d6-39ce-442e-abf0-430f036b7a3d": [
            {
                "isMain": True,
                "metadata": {
                    "bitrate": "",
                    "codec": "h264",
                    "framerate": "30.0",
                    "govlength": "",
                    "resolution": "1920x1080",
                },
                "name": "carryingcomputer_1",
                "storageLocation": "Local",
                "streamId": "24c5a7d6-39ce-442e-abf0-430f036b7a3d",
                "type": "Rtsp",
                "url": "/home/vst/vst_release/streamer_videos/carryingcomputer_1.mp4",
                "vodUrl": "/home/vst/vst_release/streamer_videos/carryingcomputer_1.mp4",
            }
        ]
    },
    {
        "490bd636-32c3-4bcf-b1a6-f185d359631c": [
            {
                "isMain": True,
                "metadata": {
                    "bitrate": "",
                    "codec": "h264",
                    "framerate": "25",
                    "govlength": "",
                    "resolution": "794x720",
                },
                "name": "its_short2",
                "storageLocation": "Local",
                "streamId": "490bd636-32c3-4bcf-b1a6-f185d359631c",
                "type": "Rtsp",
                "url": "/home/vst/vst_release/streamer_videos/its_short2.mp4",
                "vodUrl": "/home/vst/vst_release/streamer_videos/its_short2.mp4",
            }
        ]
    },
]

MOCK_TIMELINES_RESPONSE = {
    "24c5a7d6-39ce-442e-abf0-430f036b7a3d": [
        {"endTime": "2025-12-18T07:20:11.332Z", "startTime": "2025-12-18T07:19:59.332Z"}
    ],
    "490bd636-32c3-4bcf-b1a6-f185d359631c": [
        {"endTime": "2025-01-01T00:00:12.000Z", "startTime": "2025-01-01T00:00:00.000Z"}
    ],
}


class TestVSTError:
    """Test VSTError exception class."""

    def test_vst_error_is_exception(self):
        error = VSTError("Test error message")
        assert isinstance(error, Exception)

    def test_vst_error_message(self):
        error = VSTError("Custom error message")
        assert str(error) == "Custom error message"

    def test_vst_error_raise_and_catch(self):
        with pytest.raises(VSTError, match="Test error"):
            raise VSTError("Test error")


def create_mock_response(status: int, text_data: str):
    """Helper to create a mock aiohttp response."""
    mock_response = AsyncMock()
    mock_response.status = status
    mock_response.text = AsyncMock(return_value=text_data)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=None)
    return mock_response


def create_mock_session(mock_response):
    """Helper to create a mock aiohttp ClientSession."""
    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    return mock_session


async def no_retry_generator(*_args, **_kwargs):
    """A generator that yields once without retry logic."""

    class NoRetryContext:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False  # Don't suppress exceptions

    yield NoRetryContext()


class TestGetNameToStreamIdMap:
    """Test get_name_to_stream_id_map function."""

    @pytest.mark.asyncio
    async def test_successful_mapping(self):
        """Test successful retrieval of stream ID mapping."""
        mock_response = create_mock_response(200, json.dumps(MOCK_STREAMS_RESPONSE))
        mock_session = create_mock_session(mock_response)

        with (
            patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session),
            patch("vss_agents.tools.vst.utils.create_retry_strategy", side_effect=no_retry_generator),
        ):
            result = await get_name_to_stream_id_map("http://localhost:30888")

        assert "carryingcomputer_1" in result
        assert result["carryingcomputer_1"] == "24c5a7d6-39ce-442e-abf0-430f036b7a3d"
        assert "its_short2" in result
        assert result["its_short2"] == "490bd636-32c3-4bcf-b1a6-f185d359631c"

    @pytest.mark.asyncio
    async def test_handles_trailing_slash_in_url(self):
        """Test that trailing slashes in base URL are handled correctly."""
        mock_response = create_mock_response(200, json.dumps(MOCK_STREAMS_RESPONSE))
        mock_session = create_mock_session(mock_response)

        with (
            patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session),
            patch("vss_agents.tools.vst.utils.create_retry_strategy", side_effect=no_retry_generator),
        ):
            result = await get_name_to_stream_id_map("http://localhost:30888/")

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_non_200_status_raises_error(self):
        """Test that non-200 status raises RuntimeError."""
        mock_response = create_mock_response(500, "Internal Server Error")
        mock_session = create_mock_session(mock_response)

        with (
            patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session),
            patch("vss_agents.tools.vst.utils.create_retry_strategy", side_effect=no_retry_generator),
            pytest.raises(RuntimeError, match="VST streams API returned status 500"),
        ):
            await get_name_to_stream_id_map("http://localhost:30888")

    @pytest.mark.asyncio
    async def test_empty_response(self):
        """Test handling of empty response."""
        mock_response = create_mock_response(200, "[]")
        mock_session = create_mock_session(mock_response)

        with (
            patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session),
            patch("vss_agents.tools.vst.utils.create_retry_strategy", side_effect=no_retry_generator),
        ):
            result = await get_name_to_stream_id_map("http://localhost:30888")

        assert result == {}


class TestGetTimeline:
    """Test get_timeline function."""

    @pytest.mark.asyncio
    async def test_successful_timeline_retrieval(self):
        """Test successful retrieval of timeline data."""
        mock_response = create_mock_response(200, json.dumps(MOCK_TIMELINES_RESPONSE))
        mock_session = create_mock_session(mock_response)

        with (
            patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session),
            patch("vss_agents.tools.vst.utils.create_retry_strategy", side_effect=no_retry_generator),
        ):
            start_time, end_time = await get_timeline("24c5a7d6-39ce-442e-abf0-430f036b7a3d", "http://localhost:30888")

        assert start_time == "2025-12-18T07:19:59.332Z"
        assert end_time == "2025-12-18T07:20:11.332Z"

    @pytest.mark.asyncio
    async def test_timeline_with_vst_suffix_in_url(self):
        """Test that /vst suffix is properly removed from base URL."""
        mock_response = create_mock_response(200, json.dumps(MOCK_TIMELINES_RESPONSE))
        mock_session = create_mock_session(mock_response)

        with (
            patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session),
            patch("vss_agents.tools.vst.utils.create_retry_strategy", side_effect=no_retry_generator),
        ):
            start_time, end_time = await get_timeline(
                "490bd636-32c3-4bcf-b1a6-f185d359631c", "http://localhost:30888/vst"
            )

        assert start_time == "2025-01-01T00:00:00.000Z"
        assert end_time == "2025-01-01T00:00:12.000Z"

    @pytest.mark.asyncio
    async def test_timeline_not_found_and_stream_id_not_found_raises_vst_error(self):
        """Test that missing timeline and sensor name not found raises VSTError.

        When timeline is not found, get_timeline tries to convert sensor name to stream ID.
        If that also fails (sensor name not in mapping), VSTError is raised.
        """
        # Mock responses for both timelines and streams APIs
        mock_timelines_response = create_mock_response(200, json.dumps(MOCK_TIMELINES_RESPONSE))
        mock_streams_response = create_mock_response(200, json.dumps(MOCK_STREAMS_RESPONSE))

        # Create a session that returns different responses for different URLs
        call_count = [0]

        def get_side_effect(*_args, **_kwargs):
            call_count[0] += 1
            # First call is for timelines, subsequent calls are for streams
            if call_count[0] == 1:
                return mock_timelines_response
            return mock_streams_response

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=get_side_effect)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session),
            patch("vss_agents.tools.vst.utils.create_retry_strategy", side_effect=no_retry_generator),
            pytest.raises(VSTError),
        ):
            await get_timeline("non-existent-stream-id", "http://localhost:30888")

    @pytest.mark.asyncio
    async def test_timeline_with_sensor_name_converts_to_stream_id(self):
        """Test that sensor name is converted to stream ID when timeline not found initially.

        When timeline lookup fails with a sensor name, get_timeline should:
        1. Try to convert sensor name to stream ID
        2. Retry timeline lookup with the converted stream ID
        """
        mock_timelines_response = create_mock_response(200, json.dumps(MOCK_TIMELINES_RESPONSE))
        mock_streams_response = create_mock_response(200, json.dumps(MOCK_STREAMS_RESPONSE))

        # Track calls to return appropriate responses
        call_count = [0]

        def get_side_effect(*_args, **_kwargs):
            call_count[0] += 1
            # First call is for timelines (with sensor name - no timeline found)
            # Second call is for streams API
            # Third call is for timelines again (with stream ID - success)
            if call_count[0] == 1 or call_count[0] == 3:
                return mock_timelines_response
            return mock_streams_response

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=get_side_effect)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session),
            patch("vss_agents.tools.vst.utils.create_retry_strategy", side_effect=no_retry_generator),
        ):
            # Use sensor name "carryingcomputer_1" which maps to stream ID "24c5a7d6-39ce-442e-abf0-430f036b7a3d"
            start_time, end_time = await get_timeline("carryingcomputer_1", "http://localhost:30888")

        assert start_time == "2025-12-18T07:19:59.332Z"
        assert end_time == "2025-12-18T07:20:11.332Z"

    @pytest.mark.asyncio
    async def test_timeline_non_200_status_raises_error(self):
        """Test that non-200 status raises VSTError (wrapping RuntimeError)."""
        mock_response = create_mock_response(404, "Not Found")
        mock_session = create_mock_session(mock_response)

        with (
            patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session),
            patch("vss_agents.tools.vst.utils.create_retry_strategy", side_effect=no_retry_generator),
            pytest.raises(VSTError, match="VST timelines API returned status 404"),
        ):
            await get_timeline("stream-id", "http://localhost:30888")

    @pytest.mark.asyncio
    async def test_timeline_uses_env_default(self):
        """Test that VST_BASE_URL environment variable is used as default."""
        mock_response = create_mock_response(200, json.dumps(MOCK_TIMELINES_RESPONSE))
        mock_session = create_mock_session(mock_response)

        with (
            patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session),
            patch("vss_agents.tools.vst.utils.create_retry_strategy", side_effect=no_retry_generator),
            patch.dict("os.environ", {"VST_BASE_URL": "http://env-vst:30888"}),
        ):
            start_time, _end_time = await get_timeline("24c5a7d6-39ce-442e-abf0-430f036b7a3d")

        assert start_time == "2025-12-18T07:19:59.332Z"


class TestValidateVideoUrl:
    """Test validate_video_url function."""

    @pytest.mark.asyncio
    async def test_successful_head_validation(self):
        """Test successful validation with HEAD request."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"content-type": "video/mp4", "content-length": "1024000"}
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.head = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session):
            # Function returns None on success (no exception raised)
            await validate_video_url("http://example.com/video.mp4")

    @pytest.mark.asyncio
    async def test_head_fails_get_succeeds(self):
        """Test fallback to GET when HEAD fails."""
        mock_head_response = AsyncMock()
        mock_head_response.status = 405  # Method not allowed
        mock_head_response.__aenter__ = AsyncMock(return_value=mock_head_response)
        mock_head_response.__aexit__ = AsyncMock(return_value=None)

        mock_get_response = AsyncMock()
        mock_get_response.status = 206  # Partial Content
        mock_get_response.headers = {"content-type": "video/mp4", "content-length": "1024"}
        mock_get_response.__aenter__ = AsyncMock(return_value=mock_get_response)
        mock_get_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.head = MagicMock(return_value=mock_head_response)
        mock_session.get = MagicMock(return_value=mock_get_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session):
            # The function should not raise and complete successfully
            await validate_video_url("http://example.com/video.mp4")

    @pytest.mark.asyncio
    async def test_both_head_and_get_fail_raises_error(self):
        """Test that VSTError is raised when both HEAD and GET fail."""
        mock_head_response = AsyncMock()
        mock_head_response.status = 500
        mock_head_response.__aenter__ = AsyncMock(return_value=mock_head_response)
        mock_head_response.__aexit__ = AsyncMock(return_value=None)

        mock_get_response = AsyncMock()
        mock_get_response.status = 500
        mock_get_response.__aenter__ = AsyncMock(return_value=mock_get_response)
        mock_get_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.head = MagicMock(return_value=mock_head_response)
        mock_session.get = MagicMock(return_value=mock_get_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session),
            pytest.raises(VSTError, match="URL validation failed"),
        ):
            await validate_video_url("http://example.com/video.mp4")

    @pytest.mark.asyncio
    async def test_warns_on_non_video_content_type(self):
        """Test that non-video content type logs a warning but succeeds."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"content-type": "application/octet-stream", "content-length": "1024000"}
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.head = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session):
            # Function returns None on success (no exception raised)
            await validate_video_url("http://example.com/video.mp4")

    @pytest.mark.asyncio
    async def test_warns_on_zero_content_length(self):
        """Test that zero content length logs a warning but succeeds."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"content-type": "video/mp4", "content-length": "0"}
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.head = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session):
            # Function returns None on success (no exception raised)
            await validate_video_url("http://example.com/video.mp4")

    @pytest.mark.asyncio
    async def test_custom_timeout(self):
        """Test that custom timeout is respected."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"content-type": "video/mp4", "content-length": "1024000"}
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.head = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("vss_agents.tools.vst.utils.aiohttp.ClientSession", return_value=mock_session) as mock_cls:
            await validate_video_url("http://example.com/video.mp4", timeout=60)
            # Verify ClientSession was called
            mock_cls.assert_called_once()

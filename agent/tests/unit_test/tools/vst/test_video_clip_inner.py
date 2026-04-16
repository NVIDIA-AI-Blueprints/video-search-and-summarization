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
"""Tests for vst.video_clip inner function."""

import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from vss_agents.tools.vst.video_clip import VSTVideoClipConfig
from vss_agents.tools.vst.video_clip import VSTVideoClipISOInput
from vss_agents.tools.vst.video_clip import VSTVideoClipOffsetInput
from vss_agents.tools.vst.video_clip import VSTVideoClipOutput
from vss_agents.tools.vst.video_clip import get_video_url
from vss_agents.tools.vst.video_clip import vst_video_clip


class TestGetVideoUrl:
    """Test get_video_url function."""

    @pytest.mark.asyncio
    async def test_get_video_url_full_video(self):
        """Test getting full video URL without time range."""
        with patch("vss_agents.tools.vst.video_clip.get_timeline", new_callable=AsyncMock) as mock_timeline:
            mock_timeline.return_value = ("2025-01-01T00:00:00.000Z", "2025-01-01T01:00:00.000Z")

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value=json.dumps({"videoUrl": "http://vst/video.mp4"}))
            mock_response_cm = AsyncMock()
            mock_response_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response_cm.__aexit__ = AsyncMock(return_value=False)

            mock_session = MagicMock()
            mock_session.get.return_value = mock_response_cm
            mock_session_cm = AsyncMock()
            mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cm.__aexit__ = AsyncMock(return_value=False)

            with patch("vss_agents.tools.vst.video_clip.aiohttp.ClientSession", return_value=mock_session_cm):
                with patch("vss_agents.tools.vst.video_clip.create_retry_strategy") as mock_retry:
                    # Simple retry that just yields once
                    async def fake_retry(*args, **kwargs):
                        yield MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))

                    mock_retry.return_value = fake_retry()

                    result = await get_video_url("stream1", vst_internal_url="http://vst:30888")
                    assert result == "http://vst/video.mp4"

    @pytest.mark.asyncio
    async def test_get_video_url_with_time_range(self):
        """Test getting video URL with start and end time."""
        with patch("vss_agents.tools.vst.video_clip.get_timeline", new_callable=AsyncMock) as mock_timeline:
            mock_timeline.return_value = ("2025-01-01T00:00:00.000Z", "2025-01-01T01:00:00.000Z")

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value=json.dumps({"videoUrl": "http://vst/clip.mp4"}))
            mock_response_cm = AsyncMock()
            mock_response_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response_cm.__aexit__ = AsyncMock(return_value=False)

            mock_session = MagicMock()
            mock_session.get.return_value = mock_response_cm
            mock_session_cm = AsyncMock()
            mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cm.__aexit__ = AsyncMock(return_value=False)

            with patch("vss_agents.tools.vst.video_clip.aiohttp.ClientSession", return_value=mock_session_cm):
                with patch("vss_agents.tools.vst.video_clip.create_retry_strategy") as mock_retry:

                    async def fake_retry(*args, **kwargs):
                        yield MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))

                    mock_retry.return_value = fake_retry()

                    result = await get_video_url(
                        "stream1", start_time=10.0, end_time=20.0, vst_internal_url="http://vst:30888"
                    )
                    assert result == "http://vst/clip.mp4"

    @pytest.mark.asyncio
    async def test_get_video_url_with_iso_timestamps(self):
        """Test getting video URL with ISO 8601 timestamps."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value=json.dumps({"videoUrl": "http://vst/clip.mp4"}))
        mock_response_cm = AsyncMock()
        mock_response_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_response_cm
        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("vss_agents.tools.vst.video_clip.aiohttp.ClientSession", return_value=mock_session_cm):
            with patch("vss_agents.tools.vst.video_clip.create_retry_strategy") as mock_retry:

                async def fake_retry(*args, **kwargs):
                    yield MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))

                mock_retry.return_value = fake_retry()

                result = await get_video_url(
                    "stream1",
                    start_time="2025-01-01T00:00:00.000Z",
                    end_time="2025-01-01T00:10:00.000Z",
                    vst_internal_url="http://vst:30888",
                )
                assert result == "http://vst/clip.mp4"

    @pytest.mark.asyncio
    async def test_get_video_url_invalid_range(self):
        """Test error when clip end time is before start time."""
        with patch("vss_agents.tools.vst.video_clip.get_timeline", new_callable=AsyncMock) as mock_timeline:
            # 60-second timeline
            mock_timeline.return_value = ("2025-01-01T00:00:00.000Z", "2025-01-01T00:01:00.000Z")

            # end_time (5s) < start_time (30s) → clip_end < clip_start → ValueError
            with pytest.raises(ValueError, match="within the stream timeline"):
                await get_video_url("stream1", start_time=30.0, end_time=5.0, vst_internal_url="http://vst:30888")


class TestVSTVideoClipInner:
    """Test vst_video_clip inner function."""

    @pytest.fixture
    def config(self):
        return VSTVideoClipConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
        )

    @pytest.fixture
    def config_iso(self):
        return VSTVideoClipConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
            time_format="iso",
        )

    @pytest.fixture
    def config_with_overlay(self):
        return VSTVideoClipConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
            overlay_config=True,
        )

    @pytest.fixture
    def config_iso_with_overlay(self):
        return VSTVideoClipConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
            overlay_config=True,
            time_format="iso",
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_video_clip_inner(self, config, mock_builder):
        with patch("vss_agents.tools.vst.video_clip.get_stream_id", new_callable=AsyncMock) as mock_get_id:
            mock_get_id.return_value = "stream-uuid"
            with patch("vss_agents.tools.vst.video_clip.get_video_url", new_callable=AsyncMock) as mock_get_url:
                mock_get_url.return_value = "http://10.0.0.1:30888/vst/video.mp4"
                with patch("vss_agents.tools.vst.video_clip.validate_video_url", new_callable=AsyncMock):
                    gen = vst_video_clip.__wrapped__(config, mock_builder)
                    fi = await gen.__anext__()
                    inner_fn = fi.single_fn

                    inp = VSTVideoClipOffsetInput(sensor_id="camera1", start_time=10.0, end_time=20.0)
                    result = await inner_fn(inp)

                    assert isinstance(result, VSTVideoClipOutput)
                    assert "1.2.3.4:30888" in result.video_url
                    assert result.stream_id == "stream-uuid"

    @pytest.mark.asyncio
    async def test_video_clip_inner_with_iso_timestamps(self, config_iso, mock_builder):
        """Test video clip with ISO timestamps."""
        with patch("vss_agents.tools.vst.video_clip.get_stream_id", new_callable=AsyncMock) as mock_get_id:
            mock_get_id.return_value = "stream-uuid"
            with patch("vss_agents.tools.vst.video_clip.get_video_url", new_callable=AsyncMock) as mock_get_url:
                mock_get_url.return_value = "http://10.0.0.1:30888/vst/video.mp4"
                with patch("vss_agents.tools.vst.video_clip.validate_video_url", new_callable=AsyncMock):
                    gen = vst_video_clip.__wrapped__(config_iso, mock_builder)
                    fi = await gen.__anext__()
                    inner_fn = fi.single_fn

                    inp = VSTVideoClipISOInput(
                        sensor_id="camera1",
                        start_time="2025-08-25T03:05:55.752Z",
                        end_time="2025-08-25T03:06:15.752Z",
                    )
                    result = await inner_fn(inp)

                    assert isinstance(result, VSTVideoClipOutput)
                    assert "1.2.3.4:30888" in result.video_url
                    assert result.stream_id == "stream-uuid"

    @pytest.mark.asyncio
    async def test_video_clip_uses_correct_input_schema_offset(self, config, mock_builder):
        """Test that offset mode uses VSTVideoClipOffsetInput schema."""
        gen = vst_video_clip.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()
        assert fi.input_schema is VSTVideoClipOffsetInput

    @pytest.mark.asyncio
    async def test_video_clip_uses_correct_input_schema_iso(self, config_iso, mock_builder):
        """Test that iso mode uses VSTVideoClipISOInput schema."""
        gen = vst_video_clip.__wrapped__(config_iso, mock_builder)
        fi = await gen.__anext__()
        assert fi.input_schema is VSTVideoClipISOInput

    @pytest.mark.asyncio
    async def test_video_clip_inner_with_object_ids(self, config_iso_with_overlay, mock_builder):
        """Test video clip with object_ids for overlay bounding boxes."""
        with patch("vss_agents.tools.vst.video_clip.get_stream_id", new_callable=AsyncMock) as mock_get_id:
            mock_get_id.return_value = "stream-uuid"
            with patch("vss_agents.tools.vst.video_clip.get_video_url", new_callable=AsyncMock) as mock_get_url:
                mock_get_url.return_value = "http://10.0.0.1:30888/vst/video.mp4"
                with patch("vss_agents.tools.vst.video_clip.validate_video_url", new_callable=AsyncMock):
                    gen = vst_video_clip.__wrapped__(config_iso_with_overlay, mock_builder)
                    fi = await gen.__anext__()
                    inner_fn = fi.single_fn

                    inp = VSTVideoClipISOInput(
                        sensor_id="camera1",
                        start_time="2025-08-25T03:05:55.752Z",
                        end_time="2025-08-25T03:06:15.752Z",
                        object_ids=["obj-1", "obj-2"],
                    )
                    result = await inner_fn(inp)

                    assert isinstance(result, VSTVideoClipOutput)
                    assert "1.2.3.4:30888" in result.video_url
                    assert result.stream_id == "stream-uuid"

                    # Verify get_video_url was called with overlay params
                    mock_get_url.assert_called_once_with(
                        "stream-uuid",
                        "2025-08-25T03:05:55.752Z",
                        "2025-08-25T03:06:15.752Z",
                        "http://10.0.0.1:30888",
                        overlay_enabled=True,
                        object_ids=["obj-1", "obj-2"],
                    )

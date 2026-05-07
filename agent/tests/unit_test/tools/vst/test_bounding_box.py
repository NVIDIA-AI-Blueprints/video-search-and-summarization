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
"""Unit tests for bounding box overlay support in VST snapshot and video clip tools.

Tests the unified build_overlay_config helper and its integration with
both the snapshot and video_clip tools.
"""

import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import urllib.parse

import pytest

from vss_agents.tools.vst.snapshot import VSTSnapshotConfig
from vss_agents.tools.vst.snapshot import VSTSnapshotISOInput
from vss_agents.tools.vst.snapshot import VSTSnapshotOutput
from vss_agents.tools.vst.snapshot import get_snapshot_url
from vss_agents.tools.vst.snapshot import vst_snapshot
from vss_agents.tools.vst.utils import build_overlay_config
from vss_agents.tools.vst.video_clip import get_video_url


class TestBuildOverlayConfig:
    """Test the shared build_overlay_config helper function."""

    def test_overlay_disabled_returns_none(self):
        """When overlay is disabled, should return None."""
        result = build_overlay_config(overlay_enabled=False)
        assert result is None

    def test_overlay_disabled_with_object_ids_returns_none(self):
        """When overlay is disabled, should return None even with object_ids."""
        result = build_overlay_config(overlay_enabled=False, object_ids=["obj-1"])
        assert result is None

    def test_overlay_enabled_no_object_ids_shows_all(self):
        """When overlay is enabled without object_ids, showAll should be True."""
        result = build_overlay_config(overlay_enabled=True)
        assert result is not None
        decoded = json.loads(urllib.parse.unquote(result))
        assert decoded["overlay"]["bbox"]["showAll"] is True
        assert decoded["overlay"]["bbox"]["objectId"] == []
        assert decoded["overlay"]["color"] == "green"
        assert decoded["overlay"]["thickness"] == 5
        assert decoded["overlay"]["debug"] is True
        assert decoded["overlay"]["opacity"] == 254

    def test_overlay_enabled_with_empty_object_ids(self):
        """When overlay is enabled with empty list, showAll should be True."""
        result = build_overlay_config(overlay_enabled=True, object_ids=[])
        assert result is not None
        decoded = json.loads(urllib.parse.unquote(result))
        assert decoded["overlay"]["bbox"]["showAll"] is True
        assert decoded["overlay"]["bbox"]["objectId"] == []

    def test_overlay_enabled_with_object_ids(self):
        """When overlay is enabled with specific object_ids, showAll should be False."""
        result = build_overlay_config(overlay_enabled=True, object_ids=["obj-1", "obj-2"])
        assert result is not None
        decoded = json.loads(urllib.parse.unquote(result))
        assert decoded["overlay"]["bbox"]["showAll"] is False
        assert decoded["overlay"]["bbox"]["objectId"] == ["obj-1", "obj-2"]

    def test_overlay_result_is_url_encoded(self):
        """The result should be URL-encoded."""
        result = build_overlay_config(overlay_enabled=True)
        assert result is not None
        # Should be URL-encoded (contains %7B for {, etc.)
        assert "{" not in result
        assert "}" not in result
        # Should decode to valid JSON
        decoded = json.loads(urllib.parse.unquote(result))
        assert "overlay" in decoded

    def test_overlay_with_single_object_id(self):
        """Test overlay with a single object_id."""
        result = build_overlay_config(overlay_enabled=True, object_ids=["person-42"])
        decoded = json.loads(urllib.parse.unquote(result))
        assert decoded["overlay"]["bbox"]["showAll"] is False
        assert decoded["overlay"]["bbox"]["objectId"] == ["person-42"]


class TestSnapshotBoundingBox:
    """Test bounding box overlay support in the snapshot tool."""

    @pytest.fixture
    def config_with_overlay(self):
        return VSTSnapshotConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
            overlay_config=True,
            time_format="iso",
        )

    @pytest.fixture
    def config_without_overlay(self):
        return VSTSnapshotConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
            overlay_config=False,
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_get_snapshot_url_with_overlay(self):
        """Test that get_snapshot_url includes overlay param when enabled."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value=json.dumps({"imageUrl": "http://10.0.0.1:30888/vst/img.jpg"}))
        mock_response_cm = AsyncMock()
        mock_response_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_response_cm
        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("vss_agents.tools.vst.snapshot.aiohttp.ClientSession", return_value=mock_session_cm):
            with patch("vss_agents.tools.vst.snapshot.create_retry_strategy") as mock_retry:

                async def fake_retry(*args, **kwargs):
                    yield MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))

                mock_retry.return_value = fake_retry()

                result = await get_snapshot_url(
                    "stream-uuid",
                    "2025-01-01T00:05:00.000Z",
                    "http://10.0.0.1:30888",
                    overlay_enabled=True,
                )

                assert result == "http://10.0.0.1:30888/vst/img.jpg"

                # Verify the URL contained the overlay parameter
                actual_url = mock_session.get.call_args[0][0]
                assert "overlay=" in actual_url
                # Decode and verify the overlay parameter
                overlay_part = actual_url.split("overlay=")[1]
                overlay_config = json.loads(urllib.parse.unquote(overlay_part))
                assert overlay_config["overlay"]["bbox"]["showAll"] is True

    @pytest.mark.asyncio
    async def test_get_snapshot_url_without_overlay(self):
        """Test that get_snapshot_url does not include overlay param when disabled."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value=json.dumps({"imageUrl": "http://10.0.0.1:30888/vst/img.jpg"}))
        mock_response_cm = AsyncMock()
        mock_response_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_response_cm
        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("vss_agents.tools.vst.snapshot.aiohttp.ClientSession", return_value=mock_session_cm):
            with patch("vss_agents.tools.vst.snapshot.create_retry_strategy") as mock_retry:

                async def fake_retry(*args, **kwargs):
                    yield MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))

                mock_retry.return_value = fake_retry()

                result = await get_snapshot_url(
                    "stream-uuid",
                    "2025-01-01T00:05:00.000Z",
                    "http://10.0.0.1:30888",
                    overlay_enabled=False,
                )

                assert result == "http://10.0.0.1:30888/vst/img.jpg"

                # Verify the URL does NOT contain the overlay parameter
                actual_url = mock_session.get.call_args[0][0]
                assert "overlay=" not in actual_url

    @pytest.mark.asyncio
    async def test_snapshot_tool_passes_overlay_config(self, config_with_overlay, mock_builder):
        """Test that the snapshot tool passes overlay_config to get_snapshot_url."""
        with patch("vss_agents.tools.vst.snapshot.get_stream_id", new_callable=AsyncMock) as mock_get_id:
            mock_get_id.return_value = "stream-uuid"
            with patch("vss_agents.tools.vst.snapshot.get_snapshot_url", new_callable=AsyncMock) as mock_get_url:
                mock_get_url.return_value = "http://10.0.0.1:30888/vst/img.jpg"

                gen = vst_snapshot.__wrapped__(config_with_overlay, mock_builder)
                fi = await gen.__anext__()
                inner_fn = fi.single_fn

                inp = VSTSnapshotISOInput(sensor_id="camera1", start_time="2025-01-01T00:05:00.000Z")
                result = await inner_fn(inp)

                assert isinstance(result, VSTSnapshotOutput)
                # Verify overlay_enabled was passed as True
                mock_get_url.assert_called_once_with(
                    "stream-uuid",
                    "2025-01-01T00:05:00.000Z",
                    "http://10.0.0.1:30888",
                    overlay_enabled=True,
                )


class TestVideoClipBoundingBox:
    """Test bounding box overlay support in the video clip tool."""

    @pytest.mark.asyncio
    async def test_get_video_url_with_overlay_and_object_ids(self):
        """Test that get_video_url includes overlay+object_ids in the URL."""
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
                    overlay_enabled=True,
                    object_ids=["person-1", "vehicle-2"],
                )
                assert result == "http://vst/clip.mp4"

                # Verify URL contained configuration param with overlay
                actual_url = mock_session.get.call_args[0][0]
                assert "configuration=" in actual_url
                config_part = actual_url.split("configuration=")[1]
                config_data = json.loads(urllib.parse.unquote(config_part))
                assert config_data["overlay"]["bbox"]["showAll"] is False
                assert config_data["overlay"]["bbox"]["objectId"] == ["person-1", "vehicle-2"]

    @pytest.mark.asyncio
    async def test_get_video_url_with_overlay_no_object_ids(self):
        """Test that get_video_url with overlay but no object_ids shows all bboxes."""
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
                    overlay_enabled=True,
                )
                assert result == "http://vst/clip.mp4"

                actual_url = mock_session.get.call_args[0][0]
                assert "configuration=" in actual_url
                config_part = actual_url.split("configuration=")[1]
                config_data = json.loads(urllib.parse.unquote(config_part))
                assert config_data["overlay"]["bbox"]["showAll"] is True

    @pytest.mark.asyncio
    async def test_get_video_url_without_overlay(self):
        """Test that get_video_url without overlay does not include configuration param."""
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
                    overlay_enabled=False,
                )
                assert result == "http://vst/clip.mp4"

                actual_url = mock_session.get.call_args[0][0]
                assert "configuration=" not in actual_url
                assert "disableAudio=true" in actual_url

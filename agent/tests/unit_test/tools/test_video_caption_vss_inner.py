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
"""Tests for video_caption inner function (VSS path) via generator invocation."""

import os
import shutil
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from vss_agents.tools.video_caption import VideoCaptionConfig
from vss_agents.tools.video_caption import VideoCaptionInput
from vss_agents.tools.video_caption import video_caption


class TestVideoCaptionVSSInner:
    """Test the VSS path of video_caption."""

    @pytest.fixture
    def config_vss(self):
        return VideoCaptionConfig(
            llm_name="test-llm",
            use_vss=True,
            vss_backend_url="http://vss:31000",
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_vss_caption_success(self, config_vss, mock_builder):
        # Mock vst_download_tool (not available)
        mock_builder.get_tool.side_effect = [
            RuntimeError("not available"),  # vst_download
        ]

        # Set up tools for the VSS path
        mock_summarize = AsyncMock()
        mock_summarize_output = MagicMock()
        mock_summarize_output.summary = "A person walks through parking lot"
        mock_summarize.ainvoke.return_value = mock_summarize_output

        mock_upload = AsyncMock()
        mock_upload_output = MagicMock()
        mock_upload_output.file_id = "550e8400-e29b-41d4-a716-446655440000"
        mock_upload.ainvoke.return_value = mock_upload_output

        # Reconfigure builder.get_tool to return tools for vss path
        call_count = [0]

        async def get_tool_side_effect(name, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("vst_download not available")
            return None

        mock_builder.get_tool = AsyncMock(side_effect=get_tool_side_effect)

        # We need to properly mock the builder to get vss_summarize_tool and vss_file_upload_tool
        # The config references them by name, and builder.get_tool is called for each

        # Reset mock_builder setup
        mock_builder_fresh = AsyncMock()
        mock_builder_fresh.get_tool = AsyncMock(
            side_effect=[
                RuntimeError("vst_download not available"),
                mock_summarize,  # vss_summarize_tool
                mock_upload,  # vss_file_upload_tool
            ]
        )

        # Mock resolve_video_file
        with patch("vss_agents.tools.video_caption.resolve_video_file", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = ("/tmp/test_video.mp4", False)

            with patch("vss_agents.tools.video_caption.httpx.AsyncClient") as mock_httpx:
                mock_client = AsyncMock()
                mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

                gen = video_caption.__wrapped__(config_vss, mock_builder_fresh)
                fi = await gen.__anext__()
                inner_fn = fi.single_fn

                inp = VideoCaptionInput(
                    filename="video.mp4",
                    start_timestamp=10.0,
                    end_timestamp=20.0,
                    user_prompt="Describe",
                    fps=1.0,
                    video_duration=100.0,
                )
                result = await inner_fn(inp)
                assert "person" in result.lower() or "Video captions" in result

    @pytest.mark.asyncio
    async def test_vss_caption_with_cleanup(self, config_vss, mock_builder):
        mock_summarize = AsyncMock()
        mock_summarize_output = MagicMock()
        mock_summarize_output.summary = "Test summary"
        mock_summarize.ainvoke.return_value = mock_summarize_output

        mock_upload = AsyncMock()
        mock_upload_output = MagicMock()
        mock_upload_output.file_id = "550e8400-e29b-41d4-a716-446655440000"
        mock_upload.ainvoke.return_value = mock_upload_output

        mock_builder_fresh = AsyncMock()
        mock_builder_fresh.get_tool = AsyncMock(
            side_effect=[
                RuntimeError("no vst_download"),
                mock_summarize,
                mock_upload,
            ]
        )

        # Create a temp dir for cleanup test
        temp_dir = "/tmp/test_vss_cleanup_dir"
        os.makedirs(temp_dir, exist_ok=True)
        temp_file = os.path.join(temp_dir, "clip.mp4")
        with open(temp_file, "w") as f:
            f.write("fake video data")

        with patch("vss_agents.tools.video_caption.resolve_video_file", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = (temp_file, True)  # needs_cleanup=True

            with patch("vss_agents.tools.video_caption.httpx.AsyncClient") as mock_httpx:
                mock_client = AsyncMock()
                mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

                gen = video_caption.__wrapped__(config_vss, mock_builder_fresh)
                fi = await gen.__anext__()
                inner_fn = fi.single_fn

                inp = VideoCaptionInput(
                    filename="video.mp4",
                    start_timestamp=0.0,
                    end_timestamp=10.0,
                    user_prompt="test",
                    fps=1.0,
                    video_duration=30.0,
                )
                result = await inner_fn(inp)
                assert isinstance(result, str)

        # Cleanup should have been triggered
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


class TestVideoCaptionNonVSSInner:
    """Test the non-VSS (direct VLM) path of video_caption."""

    @pytest.fixture
    def config_no_vss(self):
        return VideoCaptionConfig(
            llm_name="test-llm",
            use_vss=False,
            max_retries=1,
            max_frames_per_request=5,
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_non_vss_caption(self, config_no_vss, mock_builder):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "[10.0] A person walks through a parking lot"
        mock_llm.ainvoke.return_value = mock_response
        mock_builder.get_llm.return_value = mock_llm

        # Get vst_download_tool raises (not available)
        mock_builder.get_tool.side_effect = RuntimeError("not available")

        mock_frames = ["base64frame1", "base64frame2"]

        with patch("vss_agents.tools.video_caption.resolve_video_file", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = ("/tmp/test_vid.mp4", False)

            with patch("vss_agents.utils.frame_select.frame_select", return_value=mock_frames):
                gen = video_caption.__wrapped__(config_no_vss, mock_builder)
                fi = await gen.__anext__()
                inner_fn = fi.single_fn

                inp = VideoCaptionInput(
                    filename="video.mp4",
                    start_timestamp=10.0,
                    end_timestamp=12.0,
                    user_prompt="Describe",
                    fps=1.0,
                    video_duration=100.0,
                )
                result = await inner_fn(inp)
                assert "person" in result.lower() or "Video captions" in result

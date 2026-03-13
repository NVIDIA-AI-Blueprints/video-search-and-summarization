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
"""Tests for video_detailed_caption and video_skim_caption inner functions."""

from unittest.mock import AsyncMock

import pytest
from vss_agents.tools.video_detailed_caption import VideoDetailedCaptionConfig
from vss_agents.tools.video_detailed_caption import VideoDetailedCaptionInput
from vss_agents.tools.video_detailed_caption import video_detailed_caption
from vss_agents.tools.video_skim_caption import VideoSkimCaptionConfig
from vss_agents.tools.video_skim_caption import VideoSkimCaptionInput
from vss_agents.tools.video_skim_caption import video_skim_caption


class TestVideoDetailedCaptionInner:
    """Test video_detailed_caption inner function."""

    @pytest.fixture
    def config(self):
        return VideoDetailedCaptionConfig(detailed_fps=2.0, max_video_duration=60)

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_caption_success(self, config, mock_builder):
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = "Caption: person walking"
        mock_builder.get_tool.return_value = mock_tool

        gen = video_detailed_caption.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()
        inner_fn = fi.single_fn

        inp = VideoDetailedCaptionInput(
            filename="video.mp4",
            start_timestamp=10.0,
            end_timestamp=20.0,
            user_prompt="Describe the scene",
            video_duration=100.0,
        )
        result = await inner_fn(inp)
        assert "person walking" in result

    @pytest.mark.asyncio
    async def test_duration_too_long(self, config, mock_builder):
        gen = video_detailed_caption.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()
        inner_fn = fi.single_fn

        inp = VideoDetailedCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=80.0,  # > max_video_duration of 60
            user_prompt="Describe",
            video_duration=100.0,
        )
        result = await inner_fn(inp)
        assert "too long" in result.lower()

    @pytest.mark.asyncio
    async def test_caption_tool_error(self, config, mock_builder):
        mock_tool = AsyncMock()
        mock_tool.ainvoke.side_effect = RuntimeError("VLM error")
        mock_builder.get_tool.return_value = mock_tool

        gen = video_detailed_caption.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()
        inner_fn = fi.single_fn

        inp = VideoDetailedCaptionInput(
            filename="video.mp4",
            start_timestamp=10.0,
            end_timestamp=20.0,
            user_prompt="Describe",
            video_duration=100.0,
        )
        with pytest.raises(RuntimeError, match="VLM error"):
            await inner_fn(inp)


class TestVideoSkimCaptionInner:
    """Test video_skim_caption inner function."""

    @pytest.fixture
    def config(self):
        return VideoSkimCaptionConfig(skim_fps=0.5)

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_skim_success(self, config, mock_builder):
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = "Skim: parking lot overview"
        mock_builder.get_tool.return_value = mock_tool

        gen = video_skim_caption.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()
        inner_fn = fi.single_fn

        inp = VideoSkimCaptionInput(
            filename="long_video.mp4",
            start_timestamp=0.0,
            end_timestamp=300.0,
            user_prompt="Summarize",
            video_duration=600.0,
        )
        result = await inner_fn(inp)
        assert "parking lot" in result

    @pytest.mark.asyncio
    async def test_skim_tool_error(self, config, mock_builder):
        mock_tool = AsyncMock()
        mock_tool.ainvoke.side_effect = RuntimeError("Skim error")
        mock_builder.get_tool.return_value = mock_tool

        gen = video_skim_caption.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()
        inner_fn = fi.single_fn

        inp = VideoSkimCaptionInput(
            filename="video.mp4",
            start_timestamp=0.0,
            end_timestamp=100.0,
            user_prompt="Summarize",
            video_duration=200.0,
        )
        with pytest.raises(RuntimeError, match="Skim error"):
            await inner_fn(inp)

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
"""Unit tests for vss_summarize module."""

import uuid

from pydantic import ValidationError
import pytest

from vss_agents.data_models.vss import MediaInfoOffset
from vss_agents.prompt import INIT_SUMMARIZE_PROMPT
from vss_agents.tools.vss_summarize import VSSSummarizeConfig
from vss_agents.tools.vss_summarize import VSSSummarizeInput
from vss_agents.tools.vss_summarize import VSSSummarizeOutput


class TestVSSSummarizeConfig:
    """Test VSSSummarizeConfig model."""

    def test_required_fields(self):
        config = VSSSummarizeConfig(backend_url="http://localhost:31000")
        assert config.backend_url == "http://localhost:31000"
        assert config.vss_version == "2.3.0"
        assert config.conn_timeout_ms == 5000
        assert config.read_timeout_ms == 360000
        assert config.max_concurrency == 4
        assert config.max_num_frames_per_chunk == 8

    def test_custom_values(self):
        config = VSSSummarizeConfig(
            backend_url="http://custom:8080",
            vss_version="3.0.0",
            conn_timeout_ms=10000,
            read_timeout_ms=600000,
            max_concurrency=8,
            max_num_frames_per_chunk=16,
        )
        assert config.backend_url == "http://custom:8080"
        assert config.vss_version == "3.0.0"
        assert config.conn_timeout_ms == 10000
        assert config.max_concurrency == 8

    def test_missing_backend_url_raises(self):
        with pytest.raises(ValidationError):
            VSSSummarizeConfig()


class TestVSSSummarizeInput:
    """Test VSSSummarizeInput model."""

    def test_valid_input_with_uuid(self):
        test_uuid = uuid.uuid4()
        input_data = VSSSummarizeInput(
            id=test_uuid,
            prompt="Describe the video",
            video_duration=60.0,
        )
        assert input_data.id == test_uuid
        assert input_data.prompt == "Describe the video"
        assert input_data.video_duration == 60.0
        # media_info should be auto-created
        assert input_data.media_info.start_offset == 0
        assert input_data.media_info.end_offset == 60

    def test_valid_input_with_media_info(self):
        test_uuid = uuid.uuid4()
        media_info = MediaInfoOffset(start_offset=10, end_offset=50)
        input_data = VSSSummarizeInput(
            id=test_uuid,
            prompt="Describe the video",
            video_duration=60.0,
            media_info=media_info,
        )
        assert input_data.media_info.start_offset == 10
        assert input_data.media_info.end_offset == 50

    def test_media_info_end_clamped_to_duration(self):
        test_uuid = uuid.uuid4()
        media_info = MediaInfoOffset(start_offset=10, end_offset=100)  # end > duration
        input_data = VSSSummarizeInput(
            id=test_uuid,
            prompt="Describe the video",
            video_duration=60.0,
            media_info=media_info,
        )
        # end_offset should be clamped to video_duration
        assert input_data.media_info.end_offset == 60

    def test_step_size_bounds(self):
        test_uuid = uuid.uuid4()
        # Valid step_size
        input_data = VSSSummarizeInput(
            id=test_uuid,
            prompt="Describe",
            video_duration=60.0,
            step_size=1.0,
        )
        assert input_data.step_size == 1.0

    def test_step_size_too_small_raises(self):
        test_uuid = uuid.uuid4()
        with pytest.raises(ValidationError):
            VSSSummarizeInput(
                id=test_uuid,
                prompt="Describe",
                video_duration=60.0,
                step_size=0.05,  # Less than 0.1
            )

    def test_step_size_too_large_raises(self):
        test_uuid = uuid.uuid4()
        with pytest.raises(ValidationError):
            VSSSummarizeInput(
                id=test_uuid,
                prompt="Describe",
                video_duration=60.0,
                step_size=15.0,  # Greater than 10
            )

    def test_default_prompts(self):
        test_uuid = uuid.uuid4()
        input_data = VSSSummarizeInput(
            id=test_uuid,
            prompt="Describe",
            video_duration=60.0,
        )
        assert input_data.summary_aggregation_prompt == INIT_SUMMARIZE_PROMPT["summary_aggregation_prompt"]
        assert input_data.caption_summarization_prompt == INIT_SUMMARIZE_PROMPT["caption_summarization_prompt"]

    def test_custom_prompts(self):
        test_uuid = uuid.uuid4()
        input_data = VSSSummarizeInput(
            id=test_uuid,
            prompt="Describe",
            video_duration=60.0,
            summary_aggregation_prompt="Custom aggregation prompt",
            caption_summarization_prompt="Custom summarization prompt",
        )
        assert input_data.summary_aggregation_prompt == "Custom aggregation prompt"
        assert input_data.caption_summarization_prompt == "Custom summarization prompt"

    def test_list_of_uuids(self):
        test_uuids = [uuid.uuid4(), uuid.uuid4()]
        input_data = VSSSummarizeInput(
            id=test_uuids,
            prompt="Describe multiple videos",
            video_duration=120.0,
        )
        assert input_data.id == test_uuids

    def test_prompt_max_length(self):
        test_uuid = uuid.uuid4()
        # Valid long prompt (under 5000 chars)
        long_prompt = "A" * 4999
        input_data = VSSSummarizeInput(
            id=test_uuid,
            prompt=long_prompt,
            video_duration=60.0,
        )
        assert len(input_data.prompt) == 4999

    def test_prompt_exceeds_max_length_raises(self):
        test_uuid = uuid.uuid4()
        with pytest.raises(ValidationError):
            VSSSummarizeInput(
                id=test_uuid,
                prompt="A" * 5001,  # Exceeds 5000
                video_duration=60.0,
            )


class TestVSSSummarizeOutput:
    """Test VSSSummarizeOutput model."""

    def test_valid_output(self):
        media_info = MediaInfoOffset(start_offset=0, end_offset=60)
        output = VSSSummarizeOutput(
            media_info=media_info,
            summary="This video shows a person walking.",
            step_size=1.0,
        )
        assert output.summary == "This video shows a person walking."
        assert output.step_size == 1.0

    def test_str_representation(self):
        media_info = MediaInfoOffset(start_offset=10, end_offset=50)
        output = VSSSummarizeOutput(
            media_info=media_info,
            summary="Test summary",
            step_size=0.5,
        )
        str_repr = str(output)
        assert "10 - 50" in str_repr
        assert "0.5" in str_repr
        assert "Test summary" in str_repr
        assert "timestamp:" in str_repr
        assert "step size:" in str_repr
        assert "summary:" in str_repr

    def test_step_size_none(self):
        media_info = MediaInfoOffset(start_offset=0, end_offset=60)
        output = VSSSummarizeOutput(
            media_info=media_info,
            summary="Summary without step size",
            step_size=None,
        )
        assert output.step_size is None

    def test_empty_summary(self):
        media_info = MediaInfoOffset(start_offset=0, end_offset=60)
        output = VSSSummarizeOutput(
            media_info=media_info,
            summary="",
            step_size=1.0,
        )
        assert output.summary == ""

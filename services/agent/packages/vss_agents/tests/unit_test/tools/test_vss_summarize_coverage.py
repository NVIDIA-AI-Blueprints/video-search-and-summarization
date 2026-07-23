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
"""Additional unit tests for vss_summarize module to improve coverage."""

import uuid

from pydantic import ValidationError
import pytest

from vss_agents.data_models.vss import MediaInfoOffset
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

    def test_custom_config(self):
        config = VSSSummarizeConfig(
            backend_url="http://vss:9000",
            vss_version="3.0.0",
            conn_timeout_ms=10000,
            read_timeout_ms=600000,
            max_concurrency=8,
            max_num_frames_per_chunk=16,
        )
        assert config.max_concurrency == 8
        assert config.max_num_frames_per_chunk == 16

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            VSSSummarizeConfig(
                backend_url="http://localhost:31000",
                unknown_field="value",
            )


class TestVSSSummarizeInput:
    """Test VSSSummarizeInput model."""

    def test_basic_input(self):
        file_id = uuid.uuid4()
        inp = VSSSummarizeInput(
            id=file_id,
            prompt="Describe the scene",
            video_duration=60.0,
        )
        assert inp.id == file_id
        assert inp.prompt == "Describe the scene"
        assert inp.video_duration == 60.0
        # media_info should be auto-created
        assert inp.media_info.start_offset == 0
        assert inp.media_info.end_offset == 60

    def test_with_media_info(self):
        file_id = uuid.uuid4()
        media_info = MediaInfoOffset(start_offset=10, end_offset=50)
        inp = VSSSummarizeInput(
            id=file_id,
            prompt="test",
            video_duration=60.0,
            media_info=media_info,
        )
        assert inp.media_info.start_offset == 10
        assert inp.media_info.end_offset == 50

    def test_media_info_end_capped_to_duration(self):
        file_id = uuid.uuid4()
        media_info = MediaInfoOffset(start_offset=0, end_offset=200)
        inp = VSSSummarizeInput(
            id=file_id,
            prompt="test",
            video_duration=60.0,
            media_info=media_info,
        )
        assert inp.media_info.end_offset == 60

    def test_step_size(self):
        file_id = uuid.uuid4()
        inp = VSSSummarizeInput(
            id=file_id,
            prompt="test",
            video_duration=60.0,
            step_size=1.0,
        )
        assert inp.step_size == 1.0

    def test_custom_prompts(self):
        file_id = uuid.uuid4()
        inp = VSSSummarizeInput(
            id=file_id,
            prompt="test",
            video_duration=60.0,
            caption_summarization_prompt="Custom caption prompt",
            summary_aggregation_prompt="Custom aggregation prompt",
        )
        assert inp.caption_summarization_prompt == "Custom caption prompt"
        assert inp.summary_aggregation_prompt == "Custom aggregation prompt"

    def test_list_of_ids(self):
        ids = [uuid.uuid4(), uuid.uuid4()]
        inp = VSSSummarizeInput(
            id=ids,
            prompt="test",
            video_duration=60.0,
        )
        assert len(inp.id) == 2

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            VSSSummarizeInput(
                id=uuid.uuid4(),
                prompt="test",
                video_duration=60.0,
                extra="not allowed",
            )


class TestVSSSummarizeOutput:
    """Test VSSSummarizeOutput model."""

    def test_basic_output(self):
        output = VSSSummarizeOutput(
            media_info=MediaInfoOffset(start_offset=0, end_offset=60),
            summary="The video shows a parking lot.",
            step_size=1.0,
        )
        assert "parking lot" in output.summary
        assert output.step_size == 1.0

    def test_str_representation(self):
        output = VSSSummarizeOutput(
            media_info=MediaInfoOffset(start_offset=10, end_offset=50),
            summary="Test summary",
            step_size=2.0,
        )
        result_str = str(output)
        assert "10 - 50" in result_str
        assert "Test summary" in result_str
        assert "2.0" in result_str

    def test_str_representation_no_step_size(self):
        output = VSSSummarizeOutput(
            media_info=MediaInfoOffset(start_offset=0, end_offset=30),
            summary="Summary",
        )
        result_str = str(output)
        assert "0 - 30" in result_str

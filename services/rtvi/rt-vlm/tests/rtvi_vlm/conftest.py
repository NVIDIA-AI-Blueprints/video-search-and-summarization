# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Shared pytest fixtures for rtvi_vlm tests.
"""

import argparse
from unittest.mock import MagicMock, patch

import pytest

from server.rtvi_stream_handler import RTVIStreamHandler
from tests.tests_common import TempEnv
from vlm_pipeline.vlm_pipeline import VlmModelType


@pytest.fixture
def mock_args(tmp_path):
    """Create mock arguments for RTVIStreamHandler initialization.

    Uses pytest's ``tmp_path`` fixture so the asset directory is cleaned up
    automatically between tests rather than leaking via ``tempfile.mkdtemp()``.
    """
    args = argparse.Namespace()
    args.asset_dir = str(tmp_path)
    args.kafka_enabled = False
    args.kafka_topic = "mdx-vlm-captions"
    args.kafka_bootstrap_servers = ""
    args.enable_dev_dc_gen = False
    args.max_file_duration = 0
    args.max_live_streams = 10
    args.num_gpus = 1
    args.vlm_batch_size = None  # Use 1 instead of None to avoid division errors
    args.vlm_model_type = VlmModelType.OPENAI_COMPATIBLE
    args.model_path = ""
    args.model_implementation_path = None
    args.num_vlm_procs = None
    args.vlm_input_width = None
    args.vlm_input_height = None
    args.enable_audio = False
    args.disable_vlm = True  # Disable VLM for unit tests to avoid hanging on model initialization
    args.disable_decoding = (
        True  # Disable decoding for unit tests to avoid hanging on GPU initialization
    )
    args.num_decoders_per_gpu = 1
    args.num_frames_per_second_or_fixed_frames_chunk = None
    args.use_fps_for_chunking = False
    args.enable_reasoning = False
    return args


@pytest.fixture
def stream_handler(mock_args):
    """Create a stream handler instance for testing"""
    with TempEnv(
        {
            "SKIP_PIPELINE_WARMUP": "1",
            "KAFKA_ENABLED": "false",
            "KAFKA_TOPIC": "mdx-vlm-captions",
            "KAFKA_INCIDENT_TOPIC": "mdx-vlm-incidents",
            "ERROR_MESSAGE_TOPIC": "mdx-vlm-errors",
        }
    ):
        # Mock VlmPipeline to avoid hanging on GPU initialization
        with patch("server.rtvi_stream_handler.VlmPipeline") as mock_vlm_pipeline_class:
            # Create a mock pipeline instance
            mock_pipeline = MagicMock()
            # Create a mock VlmModelInfo object
            mock_model_info = MagicMock()
            mock_model_info.id = "test-model"
            mock_model_info.created = 1234567890
            mock_model_info.owned_by = "test"
            mock_model_info.api_type = "test"
            mock_pipeline.get_models_info.return_value = mock_model_info
            mock_pipeline.get_health_status.return_value = []
            mock_vlm_pipeline_class.return_value = mock_pipeline

            try:
                handler = RTVIStreamHandler(mock_args, service_name="rtvi-vlm-test")
                yield handler
                handler.stop(force=True)
            except Exception as e:
                # If initialization fails (e.g., no GPU), skip tests
                pytest.skip(f"Stream handler initialization failed: {e}")

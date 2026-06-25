# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Unit and integration tests for RTVI Embed Server (rtvi_embed_server.py)

Tests cover:
- API endpoint functionality
- Request/response handling
- Error handling
- Health checks
- File management
- Live stream management
- Model listing
- Text embeddings generation
- Video embeddings generation
"""

import argparse
import logging
import os
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient

from server.rtvi_embed_server import RTVIServer
from tests.tests_common import TempEnv
from vlm_pipeline.vlm_pipeline import VlmModelType

API_PREFIX = "/v1"

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def mock_args():
    """Create mock arguments for RTVIServer initialization"""
    args = argparse.Namespace()

    args.asset_dir = tempfile.mkdtemp()
    args.max_asset_storage_size = None
    args.max_live_streams = 10
    args.host = "0.0.0.0"
    args.port = "8017"
    # Add any other required args from RTVIStreamHandler
    args.kafka_enabled = False
    args.kafka_topic = "mdx-embed"
    args.kafka_bootstrap_servers = ""
    args.max_file_duration = 0
    args.num_gpus = 1
    args.vlm_batch_size = 4
    args.vlm_model_type = VlmModelType("custom")
    args.model_implementation_path = "/opt/nvidia/rtvi/rtvi/models/custom/samples/cosmos-embed1"
    args.model_path = "git:https://huggingface.co/nvidia/Cosmos-Embed1-448p"
    args.model_repository_script_path = (
        "/opt/nvidia/rtvi/rtvi/models/custom/samples/cosmos-embed1/create_triton_model_repo.py"
    )
    args.num_vlm_procs = 1
    args.vlm_input_width = 448
    args.vlm_input_height = 448
    args.enable_audio = False
    args.disable_vlm = False
    args.disable_decoding = False
    args.log_level = "debug"
    args.extra_args = ""
    args.rtsp_latency = 0
    args.rtsp_timeout = 0
    args.rtsp_reconnection_interval = 5
    args.rtsp_reconnection_window = 60
    args.rtsp_reconnection_max_attempts = 10
    args.num_frames_per_second_or_fixed_frames_chunk = 8
    args.use_fps_for_chunking = False
    args.enable_reasoning = False
    args.enable_dev_dc_gen = False

    try:
        import subprocess

        count = int(subprocess.check_output(["nvdec_get_count"]).decode().strip())
        decoders = max(1, count)
    except Exception:
        decoders = 1
    args.num_decoders_per_gpu = decoders

    os.environ["RTVI_DISABLE_LIVESTREAM_PREVIEW"] = "true"
    return args


@pytest.fixture(scope="session")
def rtvi_server(mock_args):
    """Create an RTVI embed server instance for testing"""
    with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "KAFKA_ENABLED": "false"}):
        server = RTVIServer(mock_args)
        yield server
        if hasattr(server, "_stream_handler") and server._stream_handler:
            try:
                server._stream_handler.stop()
            except Exception as e:
                logger.error(f"Error stopping RTVI stream handler: {e}")


@pytest.fixture(scope="session")
def test_client(rtvi_server):
    """Create a FastAPI test client (shared across all tests)"""
    return TestClient(rtvi_server._app)


class TestHealthEndpoints:
    """Test health check endpoints"""

    def test_ready_endpoint_simple(self, test_client):
        """Test /v1/ready endpoint returns 200"""
        response = test_client.get(f"{API_PREFIX}/ready")
        assert response.status_code in [200, 503]  # May be unhealthy if model not loaded

    def test_ready_endpoint_detailed(self, test_client):
        """Test /v1/ready endpoint with detailed parameter"""
        response = test_client.get(f"{API_PREFIX}/ready?detailed=true")
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            data = response.json()
            assert "healthy" in data
            assert "checks" in data

    def test_live_endpoint(self, test_client):
        """Test /v1/live endpoint"""
        response = test_client.get(f"{API_PREFIX}/live")
        assert response.status_code in [200, 503]

    def test_startup_endpoint(self, test_client):
        """Test /v1/startup endpoint"""
        response = test_client.get(f"{API_PREFIX}/startup")
        assert response.status_code == 200
        assert "ready" in response.text.lower()

    def test_metrics_endpoint(self, test_client):
        """Test /v1/metrics endpoint"""
        response = test_client.get(f"{API_PREFIX}/metrics")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        # assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"

    def test_metadata_endpoint(self, test_client):
        """Test /v1/metadata endpoint"""
        response = test_client.get(f"{API_PREFIX}/metadata")
        assert response.status_code == 200
        assert "version" in response.json()
        assert "licenseInfo" not in response.json()


class TestNimCompatibleEndpoints:
    """NIM-compatible version, license, and manifest endpoints (parity with RTVI VLM)."""

    def test_get_version(self, test_client):
        """Test /v1/version endpoint"""
        response = test_client.get(f"{API_PREFIX}/version")
        assert response.status_code == 200
        data = response.json()
        assert "release" in data
        assert "api" in data

    def test_get_manifest(self, test_client):
        """Test /v1/manifest endpoint"""
        response = test_client.get(f"{API_PREFIX}/manifest")
        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert "model" in data


class TestModelsEndpoint:
    """Test models listing endpoint"""

    def test_list_models(self, test_client):
        """Test /v1/models endpoint"""
        response = test_client.get(f"{API_PREFIX}/models")
        assert response.status_code == 200
        data = response.json()
        assert "object" in data
        assert "data" in data
        assert isinstance(data["data"], list)


class TestFileEndpoints:
    """Test file management endpoints"""

    VIDEO_FILE_PATH = "/opt/nvidia/rtvi/warmup_streams/its_264.mp4"

    def test_list_files_empty(self, test_client):
        """Test listing files when none exist"""
        response = test_client.get(f"{API_PREFIX}/files?purpose=vision")
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)

    def test_list_files_missing_params(self, test_client):
        """Test listing files when none exist"""
        response = test_client.get(f"{API_PREFIX}/files")
        assert response.status_code == 422  # InvalidParameters
        errorMsg = response.json()["message"]
        assert errorMsg == "('query', 'purpose'): Field required"

    def test_add_file(self, test_client):
        """Test adding file"""
        files = {
            "filename": (None, self.VIDEO_FILE_PATH),
            "purpose": (None, "vision"),
            "media_type": (None, "video"),
        }
        response = test_client.post(f"{API_PREFIX}/files", files=files)
        print(f" response is {response.json()}")
        assert response.status_code == 200

    def test_add_file_missing_params(self, test_client):
        """Test adding file with missing parameters"""
        response = test_client.post(f"{API_PREFIX}/files")
        assert response.status_code == 422  # Validation error

    def test_add_file_invalid_media_type(self, test_client):
        """Test adding file with invalid media type"""
        files = {
            "file": ("test.txt", b"test content", "text/plain"),
            "purpose": (None, "vision"),
            "media_type": (None, "invalid"),
        }
        response = test_client.post(f"{API_PREFIX}/files", files=files)
        assert response.status_code in [400, 422]

    def test_get_file_info(self, test_client):
        """Test getting file info"""
        files = {
            "filename": (None, self.VIDEO_FILE_PATH),
            "purpose": (None, "vision"),
            "media_type": (None, "video"),
        }
        response = test_client.post(f"{API_PREFIX}/files", files=files)
        file_id = response.json()["id"]
        response = test_client.get(f"{API_PREFIX}/files/{file_id}")
        assert response.status_code == 200

    def test_get_file_info_not_found(self, test_client):
        """Test getting file info for non-existent file"""
        fake_id = str(uuid.uuid4())
        response = test_client.get(f"{API_PREFIX}/files/{fake_id}")
        assert response.status_code == 400

    def test_generate_video_embeddings(self, test_client):
        """Test generating video embeddings"""
        files = {
            "filename": (None, self.VIDEO_FILE_PATH),
            "purpose": (None, "vision"),
            "media_type": (None, "video"),
        }
        response = test_client.post(f"{API_PREFIX}/files", files=files)
        file_id = response.json()["id"]
        response = test_client.post(
            f"{API_PREFIX}/generate_video_embeddings",
            json={"id": file_id, "model": "cosmos-embed1-448p"},
        )
        assert response.status_code == 200

    def test_generate_video_embeddings_missing_model_param(self, test_client):
        """Test generating video embeddings with missing model parameter"""
        files = {
            "filename": (None, self.VIDEO_FILE_PATH),
            "purpose": (None, "vision"),
            "media_type": (None, "video"),
        }
        response = test_client.post(f"{API_PREFIX}/files", files=files)
        file_id = response.json()["id"]
        response = test_client.post(f"{API_PREFIX}/generate_video_embeddings", json={"id": file_id})
        assert response.status_code == 422
        assert response.json()["message"] == "('body', 'model'): Field required"

    def test_delete_file(self, test_client):
        """Test deleting file"""
        files = {
            "filename": (None, self.VIDEO_FILE_PATH),
            "purpose": (None, "vision"),
            "media_type": (None, "video"),
        }
        response = test_client.post(f"{API_PREFIX}/files", files=files)
        file_id = response.json()["id"]
        response = test_client.delete(f"{API_PREFIX}/files/{file_id}")
        assert response.status_code == 200
        assert response.json()["object"] == "file"
        assert response.json()["deleted"] is True

    def test_delete_file_not_found(self, test_client):
        """Test deleting non-existent file"""
        fake_id = str(uuid.uuid4())
        response = test_client.delete(f"{API_PREFIX}/files/{fake_id}")
        assert response.status_code == 400

    def test_get_file_content_not_found(self, test_client):
        """Test getting content for non-existent file"""
        fake_id = str(uuid.uuid4())
        response = test_client.get(f"{API_PREFIX}/files/{fake_id}/content")
        assert response.status_code == 400


class TestLiveStreamEndpoints:
    """Test live stream management endpoints"""

    def test_list_live_streams_empty(self, test_client):
        """Test listing live streams when none exist"""
        response = test_client.get(f"{API_PREFIX}/streams/get-stream-info")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_add_live_stream_missing_url(self, test_client):
        """Test adding live stream without URL"""
        response = test_client.post(
            f"{API_PREFIX}/streams/add", json={"streams": [{"description": "test"}]}
        )
        assert response.status_code == 422  # Validation error

    def test_add_live_stream_invalid_url(self, test_client):
        """Test adding live stream with invalid URL"""
        response = test_client.post(
            f"{API_PREFIX}/streams/add",
            json={"streams": [{"liveStreamUrl": "invalid://url", "description": "test"}]},
        )
        assert response.status_code in [400, 422]

    def test_delete_live_stream_not_found(self, test_client):
        """Test deleting non-existent live stream"""
        fake_id = str(uuid.uuid4())
        response = test_client.delete(f"{API_PREFIX}/streams/delete/{fake_id}")
        assert response.status_code == 400

    def test_delete_live_streams_batch(self, test_client):
        """Test batch deleting live streams"""
        fake_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        response = test_client.request(
            "DELETE", f"{API_PREFIX}/streams/delete-batch", json={"stream_ids": fake_ids}
        )
        # Should return 200 even if streams don't exist (errors in response)
        assert response.status_code == 200
        data = response.json()
        assert "deleted" in data
        assert "errors" in data


class TestTextEmbeddingsGeneration:
    """Test text embeddings generation endpoint"""

    def test_generate_text_embeddings_missing_text(self, test_client):
        """Test generating text embeddings without text input"""
        response = test_client.post(
            f"{API_PREFIX}/generate_text_embeddings", json={"model": "test-model"}
        )
        assert response.status_code == 422  # Validation error

    def test_generate_text_embeddings_missing_model(self, test_client):
        """Test generating text embeddings without model"""
        response = test_client.post(
            f"{API_PREFIX}/generate_text_embeddings", json={"text_input": "test text"}
        )
        assert response.status_code == 422  # Validation error

    def test_generate_text_embeddings_invalid_model(self, test_client):
        """Test generating text embeddings with invalid model"""
        response = test_client.post(
            f"{API_PREFIX}/generate_text_embeddings",
            json={"text_input": "test text", "model": "invalid-model"},
        )
        # Should fail validation or return error about invalid model
        assert response.status_code in [400, 422]

    def test_generate_text_embeddings(self, test_client):
        """Test generating text embeddings"""
        response = test_client.post(
            f"{API_PREFIX}/generate_text_embeddings",
            json={"text_input": "test text", "model": "cosmos-embed1-448p"},
        )
        assert response.status_code == 200
        assert isinstance(response.json()["data"][0]["embeddings"], list)

    def test_generate_text_embeddings_multiple_inputs(self, test_client):
        """Test generating text embeddings with multiple inputs"""
        response = test_client.post(
            f"{API_PREFIX}/generate_text_embeddings",
            json={"text_input": ["test text 1", "test text 2"], "model": "cosmos-embed1-448p"},
        )
        assert response.status_code == 200
        assert isinstance(response.json()["data"][0]["embeddings"], list)
        assert isinstance(response.json()["data"][1]["embeddings"], list)


class TestVideoEmbeddingsGeneration:
    """Test video embeddings generation endpoint"""

    def test_generate_video_embeddings_missing_id(self, test_client):
        """Test generating video embeddings without file ID"""
        response = test_client.post(
            f"{API_PREFIX}/generate_video_embeddings", json={"model": "test-model"}
        )
        assert response.status_code == 422  # Validation error

    def test_generate_video_embeddings_missing_model(self, test_client):
        """Test generating video embeddings without model"""
        fake_id = str(uuid.uuid4())
        response = test_client.post(f"{API_PREFIX}/generate_video_embeddings", json={"id": fake_id})
        assert response.status_code == 422  # Validation error

    def test_generate_video_embeddings_invalid_id(self, test_client):
        """Test generating video embeddings with invalid file ID"""
        response = test_client.post(
            f"{API_PREFIX}/generate_video_embeddings",
            json={"id": "invalid-uuid", "model": "test-model"},
        )
        assert response.status_code == 422  # Validation error

    def test_stop_live_stream_embeddings_not_found(self, test_client):
        """Test stopping embeddings generation for non-existent stream"""
        fake_id = str(uuid.uuid4())
        response = test_client.delete(f"{API_PREFIX}/generate_video_embeddings/{fake_id}")
        assert response.status_code == 400


class TestStreamingConstraints:
    """Test streaming implementation constraints"""

    def test_live_stream_requires_streaming(self, test_client):
        """Test that live streams require streaming=True"""
        # This would need a real live stream ID, but we test the validation logic
        fake_id = str(uuid.uuid4())
        response = test_client.post(
            f"{API_PREFIX}/generate_video_embeddings",
            json={"id": fake_id, "model": "test-model", "stream": False},
        )
        # Should fail validation or return error about live stream requiring streaming
        assert response.status_code in [400, 422]


class TestErrorHandling:
    """Test error handling and edge cases"""

    def test_invalid_json(self, test_client):
        """Test handling invalid JSON"""
        response = test_client.post(
            f"{API_PREFIX}/streams/add",
            data="invalid json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    def test_malformed_uuid(self, test_client):
        """Test handling malformed UUID"""
        response = test_client.get(f"{API_PREFIX}/files/not-a-uuid")
        assert response.status_code == 422

    def test_unsupported_method(self, test_client):
        """Test unsupported HTTP methods"""
        response = test_client.patch(f"{API_PREFIX}/files")
        assert response.status_code == 405  # Method not allowed


class TestServerInitialization:
    """Test server initialization and configuration"""

    def test_server_initialization(self, mock_args):
        """Test server can be initialized"""
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "KAFKA_ENABLED": "false"}):
            server = RTVIServer(mock_args)
            assert server._app is not None
            assert server._asset_manager is not None
            if hasattr(server, "_stream_handler") and server._stream_handler:
                server._stream_handler.stop()

    def test_argument_parser(self):
        """Test argument parser creation"""
        parser = RTVIServer.get_argument_parser()
        assert parser is not None
        # Test parsing some arguments
        args_string = " ".join(
            [
                "--asset-dir",
                tempfile.mkdtemp(),
                "--max-asset-storage-size",
                "0",
                "--max-live-streams",
                "10",
                "--host",
                "0.0.0.0",
                "--port",
                "8017",
                "--kafka-topic",
                "mdx-embed",
                "--kafka-bootstrap-servers",
                "kafka:9092",
                "--max-file-duration",
                "0",
                "--num-gpus",
                "1",
                "--vlm-batch-size",
                "4",
                "--vlm-model-type",
                "custom",
                "--model-implementation-path",
                "/opt/nvidia/rtvi/rtvi/models/custom/samples/cosmos-embed1",
                "--model-path",
                "git:https://huggingface.co/nvidia/Cosmos-Embed1-448p",
                "--model-repository-script-path",
                "/opt/nvidia/rtvi/rtvi/models/custom/samples/cosmos-embed1/create_triton_model_repo.py",
            ]
        )
        try:
            import subprocess

            count = int(subprocess.check_output(["nvdec_get_count"]).decode().strip())
            decoders = max(1, count)
        except Exception:
            decoders = 1
        args_string = args_string + " --num-decoders-per-gpu " + str(decoders)
        args = parser.parse_args(args_string.split())
        os.environ["RTVI_DISABLE_LIVESTREAM_PREVIEW"] = "true"

        assert args.host == "0.0.0.0"
        assert args.port == "8017"
        assert args.num_decoders_per_gpu == decoders


class TestIntegrationWithServer:
    """Integration tests with actual server instance"""

    @pytest.mark.skipif(
        os.getenv("SKIP_INTEGRATION_TESTS") == "1", reason="Integration tests disabled"
    )
    def test_server_startup_shutdown(self, mock_args):
        """Test server can start and stop"""
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "KAFKA_ENABLED": "false"}):
            server = RTVIServer(mock_args)
            # Note: Full server.run() would block, so we just test initialization
            assert server._app is not None
            if hasattr(server, "_stream_handler") and server._stream_handler:
                server._stream_handler.stop()

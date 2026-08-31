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
from urllib.parse import urlencode
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server.rtvi_embed_server import RTVIServer
from tests.tests_common import TempEnv
from vlm_pipeline.vlm_pipeline import VlmModelType

API_PREFIX = "/v1"

logger = logging.getLogger(__name__)

# GPU-backed session fixtures must not run under multiple xdist workers.
pytestmark = pytest.mark.xdist_group("gpu_embed")


def _config_payload(
    messagingbus="redis",
    topic_prefix="mdx-bev",
    alert_type="config",
    change="config",
    errorbus=None,
    error_topic_prefix=None,
):
    metadata = {
        "messagingbus": messagingbus,
        "region": "region-1",
        "group": "group_1",
        "topic-prefix": topic_prefix,
        "create-topic": True,
        "topic-partition": 10,
    }
    if errorbus is not None:
        metadata["errorbus"] = errorbus
    if error_topic_prefix is not None:
        metadata["error-topic-prefix"] = error_topic_prefix

    return {
        "alert_type": alert_type,
        "created_at": "2023-03-10T00:45:16Z",
        "txn_id": "f03ef248-2ec0-4a99-aeb5-938bd075bada",
        "event": {
            "camera_id": "",
            "name": "region-1--group_1",
            "camera_url": "",
            "change": change,
            "metadata": metadata,
            "headers": {
                "source": "vios",
                "created_at": "2023-03-10T00:45:16.417Z",
            },
        },
        "source": "vios",
    }


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
    args.kafka_bootstrap_servers = ""
    args.message_bus = ""
    args.message_bus_topic = "mdx-embed"
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
    with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "MESSAGE_BUS": ""}):
        server = RTVIServer(mock_args)
        yield server
        if hasattr(server, "_stream_handler") and server._stream_handler:
            server._stream_handler.stop()


@pytest.fixture(scope="session")
def test_client(rtvi_server):
    """Create a FastAPI test client (shared across all tests)"""
    return TestClient(rtvi_server._app)


@pytest.fixture
def config_rtvi_server(mock_args):
    """Create a lightweight Embed server for config endpoint tests."""
    with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "MESSAGE_BUS": ""}):
        with patch("server.rtvi_stream_handler.VlmPipeline") as mock_vlm_pipeline_class:
            mock_pipeline = MagicMock()
            mock_model_info = MagicMock()
            mock_model_info.id = "test-model"
            mock_model_info.created = 1234567890
            mock_model_info.owned_by = "test"
            mock_model_info.api_type = "test"
            mock_pipeline.get_models_info.return_value = mock_model_info
            mock_pipeline.get_health_status.return_value = []
            mock_vlm_pipeline_class.return_value = mock_pipeline
            server = RTVIServer(mock_args)
            yield server
            if hasattr(server, "_stream_handler") and server._stream_handler:
                server._stream_handler.stop()


@pytest.fixture
def config_test_client(config_rtvi_server):
    """Create a FastAPI test client for lightweight config endpoint tests."""
    return TestClient(config_rtvi_server._app)


class TestConfigEndpoint:
    """Test VSS config API endpoint."""

    def test_config_endpoint_updates_runtime_message_bus(
        self, config_test_client, config_rtvi_server
    ):
        config_rtvi_server._stream_handler.configure_message_bus = MagicMock(
            return_value={"messagingbus": "redis", "topic": "mdx-bev"}
        )

        response = config_test_client.post(f"{API_PREFIX}/config", json=_config_payload())

        assert response.status_code == 200
        assert response.json() == {
            "txn_id": "f03ef248-2ec0-4a99-aeb5-938bd075bada",
            "status": "updated",
            "messagingbus": "redis",
            "topic": "mdx-bev",
            "source": "vios",
            "created_at": "2023-03-10T00:45:16Z",
        }
        config_rtvi_server._stream_handler.configure_message_bus.assert_called_once_with(
            "redis",
            "mdx-bev",
            create_topic=True,
            topic_partition=10,
        )

    def test_config_endpoint_updates_runtime_error_bus(
        self, config_test_client, config_rtvi_server
    ):
        config_rtvi_server._stream_handler.configure_message_bus = MagicMock(
            return_value={"messagingbus": "kafka", "topic": "mdx-bev"}
        )
        config_rtvi_server._stream_handler.configure_error_bus = MagicMock(
            return_value={"errorbus": "redis", "topic": "mdx-errors"}
        )

        response = config_test_client.post(
            f"{API_PREFIX}/config",
            json=_config_payload(
                messagingbus="kafka",
                topic_prefix="mdx-bev",
                errorbus="redis",
                error_topic_prefix="mdx-errors",
            ),
        )

        assert response.status_code == 200
        assert response.json()["errorbus"] == "redis"
        assert response.json()["error_topic"] == "mdx-errors"
        config_rtvi_server._stream_handler.configure_error_bus.assert_called_once_with(
            "redis",
            "mdx-errors",
            create_topic=True,
            topic_partition=10,
        )

    def test_config_endpoint_supports_vios_path(self, config_test_client, config_rtvi_server):
        config_rtvi_server._stream_handler.configure_message_bus = MagicMock(
            return_value={"messagingbus": "kafka", "topic": "mdx-configured"}
        )

        response = config_test_client.post(
            "/api/v1/config",
            json=_config_payload(messagingbus="kafka", topic_prefix="mdx-configured"),
        )

        assert response.status_code == 200
        assert response.json()["messagingbus"] == "kafka"
        assert response.json()["topic"] == "mdx-configured"
        assert "warnings" not in response.json()

    def test_config_endpoint_returns_runtime_warning(self, config_test_client, config_rtvi_server):
        warning = (
            "Runtime message bus changed from kafka:old to redis:new while 1 media generation "
            "request(s) are active. Messages already queued may still publish to the previous "
            "route; subsequent chunk messages will use the updated route."
        )
        config_rtvi_server._stream_handler.configure_message_bus = MagicMock(
            return_value={"messagingbus": "redis", "topic": "new", "warnings": [warning]}
        )

        response = config_test_client.post(
            f"{API_PREFIX}/config",
            json=_config_payload(messagingbus="redis", topic_prefix="new"),
        )

        assert response.status_code == 200
        assert response.json()["warnings"] == [warning]

    def test_config_endpoint_rejects_non_config_change(self, config_test_client):
        response = config_test_client.post(
            f"{API_PREFIX}/config",
            json=_config_payload(change="camera_add"),
        )

        assert response.status_code == 400
        assert "Unsupported change type" in response.json()["message"]


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

    def test_add_file_from_urlencoded_form(self, test_client):
        """Test adding a file by path from an urlencoded form request."""
        response = test_client.post(
            f"{API_PREFIX}/files",
            data={
                "filename": self.VIDEO_FILE_PATH,
                "purpose": "vision",
                "media_type": "video",
            },
        )

        assert response.status_code == 200

    def test_add_file_rejects_urlencoded_form_with_too_many_fields(self, test_client):
        """Test urlencoded forms use Starlette's max_fields protection."""
        fields = [("purpose", "vision"), ("media_type", "video")]
        fields.extend((f"unused_{index}", "x") for index in range(1000))

        response = test_client.post(
            f"{API_PREFIX}/files",
            content=urlencode(fields),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )

        assert response.status_code == 400

    def test_add_file_rejects_semicolon_only_urlencoded_form(self, test_client):
        """Test semicolons are not treated as urlencoded field separators."""
        response = test_client.post(
            f"{API_PREFIX}/files",
            content="a;" * 5000,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )

        assert response.status_code == 422

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


class TestCVStreamEndpoints:
    """Test CV/VIOS-compatible stream endpoints."""

    def test_stream_add_accepts_vios_camera_streaming_file_sensor(self, mock_args, tmp_path):
        """VIOS camera_streaming file sensors are registered as file assets."""
        with TempEnv(
            {
                "SKIP_PIPELINE_WARMUP": "1",
                "MESSAGE_BUS": "",
                "FILE_URL_ALLOWED_DIRS": str(tmp_path),
            }
        ):
            with patch("server.rtvi_stream_handler.VlmPipeline") as mock_vlm_pipeline_class:
                mock_pipeline = MagicMock()
                mock_model_info = MagicMock()
                mock_model_info.id = "test-model"
                mock_model_info.created = 1234567890
                mock_model_info.owned_by = "test"
                mock_model_info.api_type = "test"
                mock_pipeline.get_models_info.return_value = mock_model_info
                mock_pipeline.get_health_status.return_value = []
                mock_vlm_pipeline_class.return_value = mock_pipeline

                rtvi_server = RTVIServer(mock_args)
                test_client = TestClient(rtvi_server._app)

                try:
                    self._assert_vios_file_sensor_roundtrip(test_client, rtvi_server, tmp_path)
                finally:
                    if hasattr(rtvi_server, "_stream_handler") and rtvi_server._stream_handler:
                        rtvi_server._stream_handler.stop()

    def _assert_vios_file_sensor_roundtrip(self, test_client, rtvi_server, tmp_path):
        camera_id = f"vios-file-{uuid.uuid4()}"
        file_path = tmp_path / "Camera_01.mp4"
        file_path.write_bytes(b"not a real mp4")
        body = {
            "alert_type": "camera_status_change",
            "created_at": "2026-07-09T15:02:40Z",
            "event": {
                "camera_id": camera_id,
                "camera_name": "Camera_01",
                "camera_url": str(file_path),
                "change": "camera_streaming",
                "camera_type": "file",
                "tags": "",
                "metadata": {
                    "duration": "600",
                    "file_start_time": "2026-07-09T14:58:40Z",
                },
            },
            "source": "vios",
        }

        response = test_client.put(f"{API_PREFIX}/camera/streaming", json=body)

        assert response.status_code == 200
        data = response.json()
        assert data["camera_id"] == camera_id
        assert data["asset_id"]
        asset = rtvi_server._asset_manager.get_asset(data["asset_id"])
        assert asset.path == str(file_path)
        assert asset.is_live is False
        assert asset.creation_time == "2026-07-09T14:58:40.000Z"
        assert rtvi_server._asset_manager.get_asset_id_by_camera_id(camera_id) == data["asset_id"]

        remove_response = test_client.request(
            "DELETE",
            f"{API_PREFIX}/camera/remove",
            json={
                "alert_type": "camera_status_change",
                "created_at": "2026-07-09T15:03:40Z",
                "event": {
                    "camera_id": camera_id,
                    "camera_name": "Camera_01",
                    "camera_url": str(file_path),
                    "change": "camera_remove",
                    "camera_type": "file",
                },
                "source": "vios",
            },
        )
        assert remove_response.status_code == 200
        assert remove_response.json()["asset_id"] == data["asset_id"]

    def test_stream_add_downloads_vios_https_file_sensor(self, mock_args):
        """VIOS HTTPS camera URL is downloaded as a file asset with request headers."""
        camera_id = f"vios-http-file-{uuid.uuid4()}"
        asset_id = str(uuid.uuid4())
        url_headers = {"Authorization": "Bearer test-token"}
        remote_url = (
            "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/"
            "Big_Buck_Bunny_360_10s_1MB.mp4"
        )

        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "MESSAGE_BUS": ""}):
            with patch("server.rtvi_stream_handler.VlmPipeline") as mock_vlm_pipeline_class:
                mock_pipeline = MagicMock()
                mock_pipeline.get_health_status.return_value = []
                mock_vlm_pipeline_class.return_value = mock_pipeline

                rtvi_server = RTVIServer(mock_args)
                test_client = TestClient(rtvi_server._app)
                download_file = AsyncMock(return_value=asset_id)
                add_live_stream = MagicMock()
                rtvi_server._asset_manager.download_file = download_file
                rtvi_server._asset_manager.add_live_stream = add_live_stream

                try:
                    response = test_client.put(
                        f"{API_PREFIX}/camera/streaming",
                        json={
                            "alert_type": "camera_status_change",
                            "created_at": "2026-07-09T15:02:40Z",
                            "event": {
                                "camera_id": camera_id,
                                "camera_name": "Camera_01",
                                "camera_url": remote_url,
                                "change": "camera_streaming",
                                "metadata": {"file_start_time": "2026-07-09T14:58:40Z"},
                                "headers": {"url_headers": url_headers},
                            },
                            "source": "vios",
                        },
                    )
                finally:
                    if hasattr(rtvi_server, "_stream_handler") and rtvi_server._stream_handler:
                        rtvi_server._stream_handler.stop()

        assert response.status_code == 200
        assert response.json()["asset_id"] == asset_id
        add_live_stream.assert_not_called()
        download_file.assert_awaited_once_with(
            url=remote_url,
            file_name="Big_Buck_Bunny_360_10s_1MB.mp4",
            purpose="vision",
            media_type="video",
            creation_time="2026-07-09T14:58:40.000Z",
            file_id=None,
            url_headers=url_headers,
            sensor_name=camera_id,
            camera_id=camera_id,
        )

    def test_stream_remove_accepts_vios_registration_without_asset(self, mock_args):
        """VIOS camera_remove is idempotent after registration-only camera_add."""
        camera_id = f"vios-reg-remove-{uuid.uuid4()}"
        register_body = {
            "alert_type": "camera_status_change",
            "created_at": "2026-07-01T07:06:11Z",
            "event": {
                "camera_id": camera_id,
                "camera_name": "Camera_01",
                "camera_url": "",
                "change": "camera_add",
                "tags": "",
            },
            "source": "vios",
        }
        remove_body = {
            "alert_type": "camera_status_change",
            "created_at": "2026-07-01T07:15:20Z",
            "event": {
                "camera_id": camera_id,
                "camera_name": "Camera_01",
                "camera_url": "",
                "change": "camera_remove",
                "tags": "",
            },
            "source": "vios",
        }

        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "MESSAGE_BUS": ""}):
            with patch("server.rtvi_stream_handler.VlmPipeline") as mock_vlm_pipeline_class:
                mock_pipeline = MagicMock()
                mock_pipeline.get_health_status.return_value = []
                mock_vlm_pipeline_class.return_value = mock_pipeline

                rtvi_server = RTVIServer(mock_args)
                test_client = TestClient(rtvi_server._app)

                try:
                    register_response = test_client.post("/api/v1/camera/add", json=register_body)
                    remove_response = test_client.request(
                        "DELETE", "/api/v1/camera/remove", json=remove_body
                    )
                finally:
                    if hasattr(rtvi_server, "_stream_handler") and rtvi_server._stream_handler:
                        rtvi_server._stream_handler.stop()

        assert register_response.status_code == 200
        assert remove_response.status_code == 200
        assert remove_response.json() == {
            "camera_id": camera_id,
            "asset_id": "",
            "status": "removed",
        }


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
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "MESSAGE_BUS": ""}):
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
                "--message-bus-topic",
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
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "MESSAGE_BUS": ""}):
            server = RTVIServer(mock_args)
            # Note: Full server.run() would block, so we just test initialization
            assert server._app is not None
            if hasattr(server, "_stream_handler") and server._stream_handler:
                server._stream_handler.stop()

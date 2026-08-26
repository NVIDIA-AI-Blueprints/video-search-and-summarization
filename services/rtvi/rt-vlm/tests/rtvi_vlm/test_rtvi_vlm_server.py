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
Unit and integration tests for RTVI VLM Server (rtvi_vlm_server.py)

Tests cover:
- API endpoint functionality
- Request/response handling
- Error handling
- Health checks
- File management
- Live stream management
- Model listing
- Caption generation
"""

import argparse
import asyncio
import os
import tempfile
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import server.rtvi_vlm_server as rtvi_vlm_server
from api_models.captions import VlmQuery
from common.chunk_info import ChunkInfo
from common.service_exception import ServiceException
from models.base_vlm_model import VlmModelOutput
from server.rtvi_stream_handler import RequestInfo
from server.rtvi_vlm_server import (
    CHAT_COMPLETION_STREAM_POLL_INTERVAL_SEC,
    GENERATE_CAPTIONS_STREAM_POLL_INTERVAL_SEC,
    RTVIServer,
    _build_chat_assistant_message,
)
from tests.tests_common import TempEnv
from vlm_pipeline.vlm_pipeline import PipelineChunkResult, VlmModelType

API_PREFIX = "/v1"


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


class TestChatCompletionFormatting:
    """Test chat completion response formatting helpers."""

    def test_stream_poll_interval_avoids_one_second_ttft_floor(self):
        assert CHAT_COMPLETION_STREAM_POLL_INTERVAL_SEC <= 0.005
        assert GENERATE_CAPTIONS_STREAM_POLL_INTERVAL_SEC <= 0.005

    def test_assistant_message_includes_think_tags_when_reasoning_is_present(self):
        message = _build_chat_assistant_message("final answer", "parsed reasoning")

        assert message.content == "<think>\nparsed reasoning\n</think>\n\nfinal answer"
        assert message.reasoning_description == "parsed reasoning"

    def test_assistant_message_allows_reasoning_only_response(self):
        message = _build_chat_assistant_message("", "parsed reasoning")

        assert message.content == "<think>\nparsed reasoning\n</think>"
        assert message.reasoning_description == "parsed reasoning"


def test_input_media_verification_timeout_rejects_non_finite_values(monkeypatch):
    warning = MagicMock()
    monkeypatch.setattr(rtvi_vlm_server.logger, "warning", warning)

    for raw_timeout in ("nan", "inf", "-inf"):
        monkeypatch.setenv(rtvi_vlm_server.INPUT_MEDIA_VERIFICATION_TIMEOUT_ENV, raw_timeout)
        assert (
            rtvi_vlm_server._input_media_verification_timeout_sec()
            == rtvi_vlm_server.DEFAULT_INPUT_MEDIA_VERIFICATION_TIMEOUT_SEC
        )

    assert warning.call_count == 3


@pytest.fixture
def mock_args():
    """Create mock arguments for RTVIServer initialization"""
    args = argparse.Namespace()
    args.asset_dir = tempfile.mkdtemp()
    args.max_asset_storage_size = None
    args.max_live_streams = 10
    args.host = "0.0.0.0"
    args.port = "8000"
    # Add any other required args from RTVIStreamHandler
    args.kafka_bootstrap_servers = ""
    args.message_bus = ""
    args.message_bus_topic = ""
    args.enable_dev_dc_gen = False
    args.max_file_duration = 0
    args.num_gpus = 1
    args.vlm_batch_size = None
    args.vlm_model_type = VlmModelType.OPENAI_COMPATIBLE
    args.model_path = ""
    args.model_implementation_path = None
    args.num_vlm_procs = None
    args.vlm_input_width = None
    args.vlm_input_height = None
    args.enable_audio = False
    args.disable_vlm = False
    args.disable_decoding = False
    args.num_decoders_per_gpu = 1
    args.num_frames_per_second_or_fixed_frames_chunk = None
    args.use_fps_for_chunking = False
    args.enable_reasoning = False
    return args


@pytest.fixture
def rtvi_server(mock_args):
    """Create an RTVI server instance for testing"""
    with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "MESSAGE_BUS": ""}):
        # Mock VlmPipeline to avoid hanging on GPU initialization
        with patch("server.rtvi_stream_handler.VlmPipeline") as mock_vlm_pipeline_class:
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
            server = RTVIServer(mock_args)
            yield server
            if hasattr(server, "_stream_handler") and server._stream_handler:
                try:
                    server._stream_handler.stop()
                except Exception:
                    pass


@pytest.fixture
def test_client(rtvi_server):
    """Create a FastAPI test client"""
    return TestClient(rtvi_server._app)


class TestConfigEndpoint:
    """Test VSS config API endpoint."""

    def test_config_endpoint_updates_runtime_message_bus(self, test_client, rtvi_server):
        rtvi_server._stream_handler.configure_message_bus = MagicMock(
            return_value={"messagingbus": "redis", "topic": "mdx-bev"}
        )

        response = test_client.post(f"{API_PREFIX}/config", json=_config_payload())

        assert response.status_code == 200
        assert response.json() == {
            "txn_id": "f03ef248-2ec0-4a99-aeb5-938bd075bada",
            "status": "updated",
            "messagingbus": "redis",
            "topic": "mdx-bev",
            "source": "vios",
            "created_at": "2023-03-10T00:45:16Z",
        }
        rtvi_server._stream_handler.configure_message_bus.assert_called_once_with(
            "redis",
            "mdx-bev",
            create_topic=True,
            topic_partition=10,
        )

    def test_config_endpoint_updates_runtime_error_bus(self, test_client, rtvi_server):
        rtvi_server._stream_handler.configure_message_bus = MagicMock(
            return_value={"messagingbus": "kafka", "topic": "mdx-bev"}
        )
        rtvi_server._stream_handler.configure_error_bus = MagicMock(
            return_value={"errorbus": "redis", "topic": "mdx-errors"}
        )

        response = test_client.post(
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
        rtvi_server._stream_handler.configure_error_bus.assert_called_once_with(
            "redis",
            "mdx-errors",
            create_topic=True,
            topic_partition=10,
        )

    def test_config_endpoint_supports_vios_path(self, test_client, rtvi_server):
        rtvi_server._stream_handler.configure_message_bus = MagicMock(
            return_value={"messagingbus": "kafka", "topic": "mdx-configured"}
        )

        response = test_client.post(
            "/api/v1/config",
            json=_config_payload(messagingbus="kafka", topic_prefix="mdx-configured"),
        )

        assert response.status_code == 200
        assert response.json()["messagingbus"] == "kafka"
        assert response.json()["topic"] == "mdx-configured"
        assert "warnings" not in response.json()

    def test_config_endpoint_returns_runtime_warning(self, test_client, rtvi_server):
        warning = (
            "Runtime message bus changed from kafka:old to redis:new while 1 media generation "
            "request(s) are active. Messages already queued may still publish to the previous "
            "route; subsequent chunk messages will use the updated route."
        )
        rtvi_server._stream_handler.configure_message_bus = MagicMock(
            return_value={"messagingbus": "redis", "topic": "new", "warnings": [warning]}
        )

        response = test_client.post(
            f"{API_PREFIX}/config",
            json=_config_payload(messagingbus="redis", topic_prefix="new"),
        )

        assert response.status_code == 200
        assert response.json()["warnings"] == [warning]

    def test_config_endpoint_rejects_non_config_change(self, test_client):
        response = test_client.post(
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
        # Content-type may vary, just check it's text/plain
        assert "text/plain" in response.headers.get("content-type", "")


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

    def test_list_files_empty(self, test_client):
        """Test listing files when none exist"""
        response = test_client.get(f"{API_PREFIX}/files?purpose=vision")
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)

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

    def test_add_file_media_verification_timeout_fails_fast(
        self, test_client, rtvi_server, tmp_path, monkeypatch
    ):
        video_path = tmp_path / "warehouse_gopro_60m_10fps.mp4"
        video_path.write_bytes(b"fake video")

        async def never_returns(*args, **kwargs):
            await asyncio.sleep(60)

        monkeypatch.setattr(rtvi_vlm_server, "_SKIP_INPUT_MEDIA_VERIFICATION", True)
        monkeypatch.setattr(rtvi_vlm_server.MediaFileInfo, "get_info_async", never_returns)
        monkeypatch.setenv("VSS_INPUT_MEDIA_VERIFICATION_TIMEOUT_SEC", "0.01")

        response = test_client.post(
            f"{API_PREFIX}/files",
            files={
                "filename": (None, str(video_path)),
                "purpose": (None, "vision"),
                "media_type": (None, "video"),
            },
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "InvalidFile"
        assert "Timed out verifying video file warehouse_gopro_60m_10fps.mp4" in body["message"]
        assert not any(
            asset.filename == video_path.name for asset in rtvi_server._asset_manager.list_assets()
        )

    def test_get_file_info_not_found(self, test_client):
        """Test getting file info for non-existent file"""
        fake_id = str(uuid.uuid4())
        response = test_client.get(f"{API_PREFIX}/files/{fake_id}")
        assert response.status_code == 400

    def test_delete_file_not_found(self, test_client):
        """Test deleting non-existent file"""
        fake_id = str(uuid.uuid4())
        response = test_client.delete(f"{API_PREFIX}/files/{fake_id}")
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

    def test_delete_live_stream_rejects_asset_in_use(self, test_client, rtvi_server):
        """Deleting a live stream in use returns 409 and does not remove the asset."""
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        asset.lock()
        rtvi_server._stream_handler.remove_rtsp_stream = MagicMock()
        rtvi_server._asset_manager.cleanup_asset = MagicMock()

        response = test_client.delete(f"{API_PREFIX}/streams/delete/{stream_id}")

        assert response.status_code == 409
        assert response.json() == {
            "code": "ResourceInUse",
            "message": f"Resource {stream_id} is currently being used",
        }
        rtvi_server._stream_handler.remove_rtsp_stream.assert_not_called()
        rtvi_server._asset_manager.cleanup_asset.assert_not_called()
        assert rtvi_server._asset_manager.get_asset(stream_id) is asset

    def test_stream_remove_rejects_active_file_sensor_without_wait(
        self, test_client, rtvi_server, tmp_path, monkeypatch
    ):
        """Removing an in-use file-backed stream returns 409 immediately."""
        monkeypatch.setenv("RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC", "30")
        file_path = tmp_path / "camera-file.mp4"
        file_path.write_bytes(b"test")
        asset_id = rtvi_server._asset_manager.add_file(
            str(file_path),
            "vision",
            "video",
            camera_id="camera-file",
        )
        asset = rtvi_server._asset_manager.get_asset(asset_id)
        asset.lock()
        rtvi_server._asset_manager.cleanup_asset = MagicMock()

        t0 = time.monotonic()
        response = test_client.post(
            f"{API_PREFIX}/stream/remove",
            json={
                "key": "sensor",
                "value": {
                    "camera_id": "camera-file",
                    "change": "camera_remove",
                },
            },
        )
        elapsed = time.monotonic() - t0

        assert response.status_code == 409
        assert response.json()["code"] == "ResourceInUse"
        assert elapsed < 1.0
        rtvi_server._asset_manager.cleanup_asset.assert_not_called()

    def test_remove_stream_openapi_documents_resource_in_use(self, test_client):
        openapi = test_client.get("/openapi.json").json()

        assert (
            openapi["paths"][f"{API_PREFIX}/streams/delete/{{stream_id}}"]["delete"]["responses"][
                "409"
            ]["description"]
            == "Resource is in use and cannot be removed."
        )
        assert (
            openapi["paths"][f"{API_PREFIX}/stream/remove"]["post"]["responses"]["409"][
                "description"
            ]
            == "Resource is in use and cannot be removed."
        )

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

    def test_add_live_stream_rejects_duplicate_stream_id(self, monkeypatch, test_client):
        """POST /v1/streams/add must reject duplicate caller-provided stream IDs."""
        import server.rtvi_vlm_server as rtvi_vlm_server

        monkeypatch.setattr(rtvi_vlm_server, "_SKIP_INPUT_MEDIA_VERIFICATION", False)
        stream_id = str(uuid.uuid4())
        body = {
            "streams": [
                {
                    "id": stream_id,
                    "liveStreamUrl": "rtsp://example.com/stream",
                    "description": "test",
                }
            ]
        }

        first_response = test_client.post(f"{API_PREFIX}/streams/add", json=body)
        assert first_response.status_code == 200
        assert first_response.json()["results"] == [{"id": stream_id}]

        duplicate_response = test_client.post(f"{API_PREFIX}/streams/add", json=body)
        assert duplicate_response.status_code == 200
        data = duplicate_response.json()
        assert data["results"] == []
        assert data["errors"][0]["error_code"] == "DuplicateStreamId"
        assert data["errors"][0]["status_code"] == 409


class TestCaptionGeneration:
    """Test caption generation endpoint"""

    def test_generate_captions_missing_id(self, test_client):
        """Test generating captions without file ID"""
        response = test_client.post(f"{API_PREFIX}/generate_captions", json={"model": "test-model"})
        assert response.status_code == 422  # Validation error

    def test_generate_captions_missing_model(self, test_client):
        """Test generating captions without model"""
        fake_id = str(uuid.uuid4())
        response = test_client.post(f"{API_PREFIX}/generate_captions", json={"id": fake_id})
        assert response.status_code == 422  # Validation error

    def test_generate_captions_invalid_id(self, test_client):
        """Test generating captions with invalid file ID"""
        response = test_client.post(
            f"{API_PREFIX}/generate_captions",
            json={"id": "invalid-uuid", "model": "test-model"},
        )
        assert response.status_code == 422  # Validation error

    @pytest.mark.parametrize("prompt", ["", " \n\t"])
    def test_generate_captions_rejects_empty_prompt(self, test_client, prompt):
        """Test generating captions rejects empty and whitespace-only prompt values."""
        fake_id = str(uuid.uuid4())
        response = test_client.post(
            f"{API_PREFIX}/generate_captions",
            json={"id": fake_id, "model": "test-model", "prompt": prompt},
        )

        assert response.status_code == 422
        assert response.json() == {
            "code": "InvalidParameters",
            "message": "prompt must not be empty",
        }

    def test_stop_live_stream_not_found(self, test_client):
        """Test stopping caption generation for non-existent stream"""
        fake_id = str(uuid.uuid4())
        response = test_client.delete(f"{API_PREFIX}/generate_captions/{fake_id}")
        assert response.status_code == 400

    def test_generate_captions_failed_request_preserves_status_code(self, test_client, rtvi_server):
        """Test completed request failures keep their original status code."""
        fake_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        error_message = (
            "Input exceeds model limits: The decoder prompt is longer than the maximum model "
            "length. Reduce frames per chunk or raise VLM_MAX_MODEL_LEN."
        )

        rtvi_server._process_vlm_request = AsyncMock(
            return_value=(request_id, MagicMock(), [MagicMock()])
        )

        req_info = RequestInfo(request_id=request_id)
        req_info.status = RequestInfo.Status.FAILED
        req_info.error_message = error_message
        req_info.error_status_code = 400

        rtvi_server._stream_handler.wait_for_request_done = MagicMock()
        rtvi_server._stream_handler.get_response = MagicMock(return_value=(req_info, []))
        rtvi_server._stream_handler._send_error_message_to_kafka = MagicMock()

        response = test_client.post(
            f"{API_PREFIX}/generate_captions",
            json={"id": fake_id, "model": "test-model", "prompt": "Describe the video."},
        )

        assert response.status_code == 400
        assert response.json() == {"code": "RequestError", "message": error_message}
        rtvi_server._stream_handler._send_error_message_to_kafka.assert_not_called()

    def test_process_live_vlm_request_creates_independent_request(self, rtvi_server):
        """A second live generate request must not reconnect to an active one."""
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)

        existing_req = RequestInfo()
        existing_req.is_live = True
        existing_req.status = RequestInfo.Status.PROCESSING
        existing_req.assets = [asset]
        rtvi_server._stream_handler._request_info_map[existing_req.request_id] = existing_req

        rtvi_server._stream_handler.generate_vlm_captions = MagicMock(return_value="new-request")
        query = VlmQuery(
            id=uuid.UUID(stream_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )

        request_id, returned_asset, asset_list = asyncio.run(
            rtvi_server._process_vlm_request(
                query,
                [stream_id],
                log_prefix="generate_captions",
            )
        )

        assert request_id == "new-request"
        assert returned_asset is asset
        assert asset_list == []
        rtvi_server._stream_handler.generate_vlm_captions.assert_called_once_with(
            [asset],
            query,
            True,
        )

    def test_vlm_server_rtsp_generate_reuses_pipeline_stream_for_same_asset(self, rtvi_server):
        """Multiple caption requests use the same RTSP asset in the pipeline."""
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        query = VlmQuery(
            id=uuid.UUID(stream_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )

        first_request_id = rtvi_server._stream_handler.generate_vlm_captions(
            [asset],
            query,
            is_rtsp=True,
        )
        second_request_id = rtvi_server._stream_handler.generate_vlm_captions(
            [asset],
            query,
            is_rtsp=True,
        )

        assert first_request_id != second_request_id
        assert asset.use_count == 2

        live_requests = [
            req_info
            for req_info in rtvi_server._stream_handler._request_info_map.values()
            if req_info.is_live and req_info.assets and req_info.assets[0] is asset
        ]
        assert {req.request_id for req in live_requests} == {
            first_request_id,
            second_request_id,
        }
        assert all(not hasattr(req, "pipeline_stream_id") for req in live_requests)

        pipeline_calls = rtvi_server._stream_handler._vlm_pipeline.add_live_stream.call_args_list
        assert [call.kwargs["asset"] for call in pipeline_calls] == [asset, asset]
        assert [call.kwargs["asset"].asset_id for call in pipeline_calls] == [
            stream_id,
            stream_id,
        ]
        assert [call.kwargs["request_id"] for call in pipeline_calls] == [
            first_request_id,
            second_request_id,
        ]

    def test_vlm_server_rtsp_stop_finishes_all_active_caption_requests(self, rtvi_server):
        """Stopping by asset ID drains the stream and finishes every subscriber."""
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        query = VlmQuery(
            id=uuid.UUID(stream_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        first_request_id = rtvi_server._stream_handler.generate_vlm_captions(
            [asset],
            query,
            is_rtsp=True,
        )
        second_request_id = rtvi_server._stream_handler.generate_vlm_captions(
            [asset],
            query,
            is_rtsp=True,
        )

        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream.return_value = 0.05
        rtvi_server._stream_handler._safe_rmtree = MagicMock()

        first_request = rtvi_server._stream_handler._request_info_map[first_request_id]
        second_request = rtvi_server._stream_handler._request_info_map[second_request_id]

        rtvi_server._stream_handler.remove_rtsp_stream(asset)

        assert asset.use_count == 0
        assert first_request_id not in rtvi_server._stream_handler._request_info_map
        assert second_request_id not in rtvi_server._stream_handler._request_info_map
        assert first_request.status == RequestInfo.Status.SUCCESSFUL
        assert second_request.status == RequestInfo.Status.SUCCESSFUL
        assert first_request.status_event.is_set()
        assert second_request.status_event.is_set()
        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream.assert_called_once_with(
            stream_id,
            timeout_sec=None,
        )
        rtvi_server._stream_handler._safe_rmtree.assert_called_once_with(
            f"/tmp/rtvi/cached_frames/{stream_id}"
        )

    def test_vlm_server_rtsp_stop_single_caption_request_uses_asset_id(self, rtvi_server):
        """Stopping one live caption request drains the shared RTSP asset ID."""
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        query = VlmQuery(
            id=uuid.UUID(stream_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        request_id = rtvi_server._stream_handler.generate_vlm_captions(
            [asset],
            query,
            is_rtsp=True,
        )
        req_info = rtvi_server._stream_handler._request_info_map[request_id]

        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream.return_value = 0.05
        rtvi_server._stream_handler._safe_rmtree = MagicMock()

        rtvi_server._stream_handler.remove_rtsp_stream(asset)

        assert asset.use_count == 0
        assert request_id not in rtvi_server._stream_handler._request_info_map
        assert req_info.status == RequestInfo.Status.SUCCESSFUL
        assert req_info.status_event.is_set()
        assert req_info._live_stop_finalized is True
        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream.assert_called_once_with(
            stream_id,
            timeout_sec=None,
        )
        rtvi_server._stream_handler._safe_rmtree.assert_called_once_with(
            f"/tmp/rtvi/cached_frames/{stream_id}"
        )

    def test_vlm_server_rejects_new_caption_request_while_stream_is_stopping(self, rtvi_server):
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        query = VlmQuery(
            id=uuid.UUID(stream_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        rtvi_server._stream_handler._live_streams_stopping.add(stream_id)

        with pytest.raises(ServiceException) as exc_info:
            rtvi_server._stream_handler.generate_vlm_captions(
                [asset],
                query,
                is_rtsp=True,
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "ResourceInUse"
        assert asset.use_count == 0
        assert not rtvi_server._stream_handler._get_live_stream_requests(stream_id)

    def test_live_request_finalization_is_idempotent_across_eos_and_delete(self, rtvi_server):
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        req_info = RequestInfo()
        req_info.is_live = True
        req_info.status = RequestInfo.Status.PROCESSING
        req_info.assets = [asset]
        asset.lock()

        active_counter = MagicMock()
        rtvi_server._stream_handler._metrics._active_live_streams_counter = active_counter
        rtvi_server._stream_handler.stop_request_profiling = MagicMock()

        rtvi_server._stream_handler._process_output(req_info, True, [])
        rtvi_server._stream_handler._finish_stopped_live_caption_request(
            req_info,
            was_processing=True,
        )

        active_counter.add.assert_called_once_with(-1)
        assert asset.use_count == 0
        assert req_info.status == RequestInfo.Status.SUCCESSFUL
        assert req_info.status_event.is_set()

    def test_rtsp_stop_failure_preserves_request_for_retry(self, rtvi_server):
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        query = VlmQuery(
            id=uuid.UUID(stream_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        request_id = rtvi_server._stream_handler.generate_vlm_captions(
            [asset],
            query,
            is_rtsp=True,
        )
        req_info = rtvi_server._stream_handler._request_info_map[request_id]
        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream.side_effect = [
            RuntimeError("drain failed"),
            0.05,
        ]
        rtvi_server._stream_handler._safe_rmtree = MagicMock()

        with pytest.raises(RuntimeError, match="drain failed"):
            rtvi_server._stream_handler.remove_rtsp_stream(asset)

        assert asset.use_count == 1
        assert request_id in rtvi_server._stream_handler._request_info_map
        assert req_info.status == RequestInfo.Status.PROCESSING
        assert not req_info.status_event.is_set()
        assert req_info._live_stop_finalized is False
        assert stream_id in rtvi_server._stream_handler._live_streams_cleanup_required

        with pytest.raises(ServiceException) as exc_info:
            rtvi_server._stream_handler.generate_vlm_captions(
                [asset],
                query,
                is_rtsp=True,
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "ResourceInUse"

        rtvi_server._stream_handler.remove_rtsp_stream(asset)

        assert asset.use_count == 0
        assert request_id not in rtvi_server._stream_handler._request_info_map
        assert req_info.status == RequestInfo.Status.SUCCESSFUL
        assert req_info.status_event.is_set()
        assert req_info._live_stop_finalized is True
        assert stream_id not in rtvi_server._stream_handler._live_streams_cleanup_required
        rtvi_server._stream_handler._safe_rmtree.assert_called_once_with(
            f"/tmp/rtvi/cached_frames/{stream_id}"
        )

    def test_live_request_finalization_releases_assets_when_profiling_fails(self, rtvi_server):
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        req_info = RequestInfo()
        req_info.is_live = True
        req_info.status = RequestInfo.Status.PROCESSING
        req_info.assets = [asset]
        req_info._request_metrics = object()
        asset.lock()
        rtvi_server._stream_handler.stop_request_profiling = MagicMock(
            side_effect=RuntimeError("profiling export failed")
        )

        rtvi_server._stream_handler._finish_stopped_live_caption_request(
            req_info,
            was_processing=True,
        )

        assert asset.use_count == 0
        assert req_info.status == RequestInfo.Status.SUCCESSFUL
        assert req_info.status_event.is_set()

    def test_vlm_server_rtsp_stop_one_caption_request_by_request_id_keeps_other_active(
        self, rtvi_server
    ):
        """Stopping by request ID removes one subscriber and leaves the stream active."""
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        query = VlmQuery(
            id=uuid.UUID(stream_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        first_request_id = rtvi_server._stream_handler.generate_vlm_captions(
            [asset],
            query,
            is_rtsp=True,
        )
        second_request_id = rtvi_server._stream_handler.generate_vlm_captions(
            [asset],
            query,
            is_rtsp=True,
        )

        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream_subscriber.return_value = True
        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream.return_value = 0.05
        rtvi_server._stream_handler._safe_rmtree = MagicMock()

        rtvi_server._stream_handler.remove_rtsp_stream_request(asset, first_request_id)

        assert asset.use_count == 1
        assert first_request_id not in rtvi_server._stream_handler._request_info_map
        assert second_request_id in rtvi_server._stream_handler._request_info_map
        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream_subscriber.assert_called_once_with(
            stream_id,
            first_request_id,
        )
        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream.assert_not_called()
        rtvi_server._stream_handler._safe_rmtree.assert_not_called()

    def test_rtsp_stop_final_request_failure_preserves_request_for_retry(self, rtvi_server):
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        query = VlmQuery(
            id=uuid.UUID(stream_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        request_id = rtvi_server._stream_handler.generate_vlm_captions(
            [asset],
            query,
            is_rtsp=True,
        )
        req_info = rtvi_server._stream_handler._request_info_map[request_id]
        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream.side_effect = [
            RuntimeError("drain failed"),
            0.05,
        ]
        rtvi_server._stream_handler._safe_rmtree = MagicMock()

        with pytest.raises(RuntimeError, match="drain failed"):
            rtvi_server._stream_handler.remove_rtsp_stream_request(asset, request_id)

        assert asset.use_count == 1
        assert request_id in rtvi_server._stream_handler._request_info_map
        assert req_info.status == RequestInfo.Status.PROCESSING
        assert not req_info.status_event.is_set()
        assert req_info._live_stop_finalized is False
        assert stream_id in rtvi_server._stream_handler._live_streams_cleanup_required
        assert stream_id not in rtvi_server._stream_handler._live_streams_stopping

        rtvi_server._stream_handler.remove_rtsp_stream_request(asset, request_id)

        assert asset.use_count == 0
        assert request_id not in rtvi_server._stream_handler._request_info_map
        assert req_info.status == RequestInfo.Status.SUCCESSFUL
        assert req_info.status_event.is_set()
        assert req_info._live_stop_finalized is True
        assert stream_id not in rtvi_server._stream_handler._live_streams_cleanup_required
        rtvi_server._stream_handler._safe_rmtree.assert_called_once_with(
            f"/tmp/rtvi/cached_frames/{stream_id}"
        )

    def test_rtsp_stop_subscriber_failure_preserves_request_for_retry(self, rtvi_server):
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        query = VlmQuery(
            id=uuid.UUID(stream_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        first_request_id = rtvi_server._stream_handler.generate_vlm_captions(
            [asset],
            query,
            is_rtsp=True,
        )
        second_request_id = rtvi_server._stream_handler.generate_vlm_captions(
            [asset],
            query,
            is_rtsp=True,
        )
        first_request = rtvi_server._stream_handler._request_info_map[first_request_id]
        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream_subscriber.side_effect = [
            RuntimeError("subscriber removal failed"),
            True,
        ]

        with pytest.raises(RuntimeError, match="subscriber removal failed"):
            rtvi_server._stream_handler.remove_rtsp_stream_request(asset, first_request_id)

        assert asset.use_count == 2
        assert first_request_id in rtvi_server._stream_handler._request_info_map
        assert second_request_id in rtvi_server._stream_handler._request_info_map
        assert first_request.status == RequestInfo.Status.PROCESSING
        assert not first_request.status_event.is_set()
        assert stream_id not in rtvi_server._stream_handler._live_streams_stopping
        assert stream_id not in rtvi_server._stream_handler._live_streams_cleanup_required

        rtvi_server._stream_handler.remove_rtsp_stream_request(asset, first_request_id)

        assert asset.use_count == 1
        assert first_request_id not in rtvi_server._stream_handler._request_info_map
        assert second_request_id in rtvi_server._stream_handler._request_info_map
        assert first_request.status == RequestInfo.Status.SUCCESSFUL
        assert first_request.status_event.is_set()
        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream.assert_not_called()

    def test_stop_live_stream_with_request_id_keeps_other_caption_requests_active(
        self, test_client, rtvi_server
    ):
        """DELETE /generate_captions/{stream_id}?request_id=... stops only that request."""
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        query = VlmQuery(
            id=uuid.UUID(stream_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        first_request_id = rtvi_server._stream_handler.generate_vlm_captions(
            [asset],
            query,
            is_rtsp=True,
        )
        second_request_id = rtvi_server._stream_handler.generate_vlm_captions(
            [asset],
            query,
            is_rtsp=True,
        )

        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream_subscriber.return_value = True
        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream.return_value = 0.05

        response = test_client.delete(
            f"{API_PREFIX}/generate_captions/{stream_id}?request_id={first_request_id}"
        )

        assert response.status_code == 200
        assert asset.use_count == 1
        assert first_request_id not in rtvi_server._stream_handler._request_info_map
        assert second_request_id in rtvi_server._stream_handler._request_info_map
        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream_subscriber.assert_called_once_with(
            stream_id,
            first_request_id,
        )
        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream.assert_not_called()

    def test_vlm_server_rtsp_generate_reports_original_stream_id(self, rtvi_server):
        """Live caption chunks report the original RTSP asset ID."""
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        query = VlmQuery(
            id=uuid.UUID(stream_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        request_id = rtvi_server._stream_handler.generate_vlm_captions(
            [asset],
            query,
            is_rtsp=True,
        )
        req_info = rtvi_server._stream_handler._request_info_map[request_id]
        chunk = ChunkInfo(
            streamId=request_id,
            chunkIdx=0,
            file=asset.path,
            start_ntp="2026-05-27T00:00:00.000Z",
            end_ntp="2026-05-27T00:00:10.000Z",
        )
        chunk_result = PipelineChunkResult(
            chunk=chunk,
            vlm_model_output=VlmModelOutput(output="ok", input_tokens=1, output_tokens=1),
        )
        rtvi_server._stream_handler._process_output = MagicMock()

        rtvi_server._stream_handler._on_vlm_chunk_response(chunk_result, req_info)

        assert chunk.streamId == stream_id
        rtvi_server._stream_handler._process_output.assert_called_once_with(
            req_info,
            False,
            [chunk_result],
        )

    def test_delete_live_stream_rejects_active_request_without_wait(
        self, test_client, rtvi_server, monkeypatch
    ):
        """Deleting an in-use stream must not wait for the setup drain timeout."""
        monkeypatch.setenv("RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC", "30")
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        asset.lock()
        rtvi_server._stream_handler.remove_rtsp_stream = MagicMock()

        t0 = time.monotonic()
        response = test_client.delete(f"{API_PREFIX}/streams/delete/{stream_id}")
        elapsed = time.monotonic() - t0

        assert response.status_code == 409
        assert response.json()["code"] == "ResourceInUse"
        assert elapsed < 1.0
        rtvi_server._stream_handler.remove_rtsp_stream.assert_not_called()

    def test_stop_live_stream_finishes_multiple_active_requests(
        self, test_client, rtvi_server, monkeypatch
    ):
        """Stopping by asset ID drains and finishes all subscribers."""
        monkeypatch.setenv("RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC", "30")
        stream_id = rtvi_server._asset_manager.add_live_stream("rtsp://example.com/live")
        asset = rtvi_server._asset_manager.get_asset(stream_id)
        query = VlmQuery(
            id=uuid.UUID(stream_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        rtvi_server._stream_handler.generate_vlm_captions([asset], query, is_rtsp=True)
        rtvi_server._stream_handler.generate_vlm_captions([asset], query, is_rtsp=True)
        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream.return_value = 0.05

        response = test_client.delete(f"{API_PREFIX}/generate_captions/{stream_id}")

        assert response.status_code == 200
        assert asset.use_count == 0
        assert not rtvi_server._stream_handler._get_live_stream_requests(stream_id)
        rtvi_server._stream_handler._vlm_pipeline.remove_live_stream.assert_called_once_with(
            stream_id,
            timeout_sec=None,
        )


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
            # Mock VlmPipeline to avoid hanging on GPU initialization
            with patch("server.rtvi_stream_handler.VlmPipeline") as mock_vlm_pipeline_class:
                mock_pipeline = MagicMock()
                mock_pipeline.get_models_info.return_value = {"object": "list", "data": []}
                mock_vlm_pipeline_class.return_value = mock_pipeline
                server = RTVIServer(mock_args)
                assert server._app is not None
                assert server._asset_manager is not None
                if hasattr(server, "_stream_handler") and server._stream_handler:
                    server._stream_handler.stop()

    def test_argument_parser(self):
        """Test argument parser creation"""
        parser = RTVIServer.get_argument_parser()
        assert parser is not None
        # Test parsing some arguments (include required --vlm-model-type)
        args = parser.parse_args(
            ["--host", "127.0.0.1", "--port", "9000", "--vlm-model-type", "openai-compat"]
        )
        assert args.host == "127.0.0.1"
        assert args.port == "9000"
        assert args.vlm_model_type == VlmModelType.OPENAI_COMPATIBLE


class TestStreamingConstraints:
    """Test streaming implementation constraints"""

    def test_live_stream_requires_streaming(self, test_client):
        """Test that live streams require streaming=True"""
        # This would need a real live stream ID, but we test the validation logic
        fake_id = str(uuid.uuid4())
        response = test_client.post(
            f"{API_PREFIX}/generate_captions",
            json={"id": fake_id, "model": "test-model", "stream": False},
        )
        # Should fail validation or return error about live stream requiring streaming
        assert response.status_code in [400, 422]


class TestCVStreamEndpoints:
    """Test CV-compatible stream endpoints."""

    def test_stream_add_accepts_vios_camera_add_registration(self, test_client):
        """VIOS camera_add without a URL is accepted as a registration event."""
        camera_id = f"vios-reg-{uuid.uuid4()}"
        body = {
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

        response = test_client.post("/api/v1/camera/add", json=body)

        assert response.status_code == 200
        data = response.json()
        assert data["camera_id"] == camera_id
        assert data["asset_id"] == ""
        assert data["status"] == "added"
        assert data["inference"] is False

    def test_stream_add_accepts_vios_camera_streaming_file_sensor(
        self, test_client, rtvi_server, monkeypatch, tmp_path
    ):
        """VIOS camera_streaming accepts file sensors with plain absolute paths."""
        camera_id = f"vios-file-{uuid.uuid4()}"
        file_path = tmp_path / "Camera_01.mp4"
        file_path.write_bytes(b"not a real mp4")
        monkeypatch.setenv("FILE_URL_ALLOWED_DIRS", str(tmp_path))
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
                    "metadata": {"file_start_time": "2026-07-09T14:58:40Z"},
                },
                "source": "vios",
            },
        )
        assert remove_response.status_code == 200
        assert remove_response.json()["asset_id"] == data["asset_id"]

    def test_stream_add_file_auto_inference_uses_non_streaming_output(
        self, rtvi_server, monkeypatch, tmp_path
    ):
        """VIOS file auto-inference must publish completed chunks to the output bus."""
        camera_id = f"vios-file-auto-{uuid.uuid4()}"
        file_path = tmp_path / "Camera_01.mp4"
        file_path.write_bytes(b"not a real mp4")
        monkeypatch.setenv("FILE_URL_ALLOWED_DIRS", str(tmp_path))
        process_request = AsyncMock(return_value=("request-id", MagicMock(), []))
        rtvi_server._process_vlm_request = process_request
        client = TestClient(rtvi_server._app)

        response = client.put(
            f"{API_PREFIX}/camera/streaming",
            json={
                "alert_type": "camera_status_change",
                "created_at": "2026-08-19T13:21:00Z",
                "event": {
                    "camera_id": camera_id,
                    "camera_name": "Camera_01",
                    "camera_url": str(file_path),
                    "change": "camera_streaming",
                    "camera_type": "file",
                    "metadata": {
                        "file_start_time": "2026-08-19T13:21:00Z",
                        "prompt": "Describe the video.",
                    },
                },
                "source": "vios",
            },
        )

        assert response.status_code == 200
        assert response.json()["inference"] is True
        query = process_request.await_args.args[0]
        assert query.stream is False

    def test_stream_add_downloads_vios_https_file_sensor(
        self, test_client, rtvi_server, monkeypatch
    ):
        """VIOS HTTPS camera URL is downloaded as a file asset with request headers."""
        camera_id = f"vios-http-file-{uuid.uuid4()}"
        asset_id = str(uuid.uuid4())
        url_headers = {"Authorization": "Bearer test-token"}
        remote_url = (
            "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/"
            "Big_Buck_Bunny_360_10s_1MB.mp4"
        )
        download_file = AsyncMock(return_value=asset_id)
        add_live_stream = MagicMock()
        monkeypatch.setattr(rtvi_server._asset_manager, "download_file", download_file)
        monkeypatch.setattr(rtvi_server._asset_manager, "add_live_stream", add_live_stream)

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

    def test_stream_add_rejects_vios_file_sensor_outside_allowlist(
        self, test_client, monkeypatch, tmp_path
    ):
        """VIOS file sensors use the same FILE_URL_ALLOWED_DIRS guard as file:// URLs."""
        allowed_dir = tmp_path / "allowed"
        allowed_dir.mkdir()
        outside_file = tmp_path / "outside.mp4"
        outside_file.write_bytes(b"not a real mp4")
        monkeypatch.setenv("FILE_URL_ALLOWED_DIRS", str(allowed_dir))

        response = test_client.put(
            f"{API_PREFIX}/camera/streaming",
            json={
                "alert_type": "camera_status_change",
                "created_at": "2026-07-09T15:02:40Z",
                "event": {
                    "camera_id": f"vios-file-denied-{uuid.uuid4()}",
                    "camera_name": "Camera_01",
                    "camera_url": str(outside_file),
                    "change": "camera_streaming",
                    "camera_type": "file",
                },
                "source": "vios",
            },
        )

        assert response.status_code == 403

    def test_stream_add_rejects_vios_invalid_alert_type(self, test_client):
        """VIOS payloads must use the camera status alert type."""
        response = test_client.put(
            f"{API_PREFIX}/camera/streaming",
            json={
                "alert_type": "config",
                "created_at": "2026-07-01T07:06:11Z",
                "event": {
                    "camera_id": "cam-invalid-alert",
                    "camera_name": "Camera_01",
                    "camera_url": "rtsp://example.com/stream",
                    "change": "camera_streaming",
                    "camera_type": "rtsp",
                    "tags": "",
                },
                "source": "vios",
            },
        )

        assert response.status_code == 400

    def test_stream_remove_accepts_vios_payload(self, test_client):
        """VIOS camera_remove removes streams by camera_id."""
        camera_id = f"vios-remove-{uuid.uuid4()}"
        add_response = test_client.put(
            f"{API_PREFIX}/camera/streaming",
            json={
                "alert_type": "camera_status_change",
                "created_at": "2026-07-01T06:30:17Z",
                "event": {
                    "camera_id": camera_id,
                    "camera_name": "boxcart_1",
                    "camera_url": "rtsp://10.24.216.43:30555/live/test",
                    "change": "camera_streaming",
                    "camera_type": "rtsp",
                },
                "source": "vios",
            },
        )
        assert add_response.status_code == 200
        asset_id = add_response.json()["asset_id"]

        remove_response = test_client.request(
            "DELETE",
            f"{API_PREFIX}/camera/remove",
            json={
                "alert_type": "camera_status_change",
                "created_at": "2026-07-01T07:15:20Z",
                "event": {
                    "camera_id": camera_id,
                    "camera_name": "boxcart_1",
                    "camera_url": "rtsp://10.24.216.43:30555/live/test",
                    "change": "camera_remove",
                    "tags": "",
                },
                "source": "vios",
            },
        )

        assert remove_response.status_code == 200
        data = remove_response.json()
        assert data["camera_id"] == camera_id
        assert data["asset_id"] == asset_id

    def test_stream_remove_accepts_vios_registration_without_asset(self, test_client):
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

        register_response = test_client.post("/api/v1/camera/add", json=register_body)
        remove_response = test_client.request("DELETE", "/api/v1/camera/remove", json=remove_body)

        assert register_response.status_code == 200
        assert remove_response.status_code == 200
        assert remove_response.json() == {
            "camera_id": camera_id,
            "asset_id": "",
            "status": "removed",
        }

    def test_stream_add_rejects_duplicate_camera_id(self, test_client):
        """POST /v1/stream/add must reject duplicate CV camera IDs."""
        body = {
            "key": "sensor",
            "value": {
                "camera_id": "cam-001",
                "camera_url": "rtsp://example.com/stream",
                "change": "camera_add",
            },
        }

        first_response = test_client.post(f"{API_PREFIX}/stream/add", json=body)
        assert first_response.status_code == 200

        duplicate_response = test_client.post(f"{API_PREFIX}/stream/add", json=body)
        assert duplicate_response.status_code == 409
        assert duplicate_response.json()["code"] == "DuplicateCameraId"

    def test_stream_add_rejects_duplicate_camera_id_with_metadata(self, rtvi_server):
        """Duplicate CV camera IDs are rejected before auto-inference starts again."""
        rtvi_server._process_vlm_request = AsyncMock(return_value=("request-id", None, []))
        client = TestClient(rtvi_server._app)
        body = {
            "key": "sensor",
            "value": {
                "camera_id": "cam-001",
                "camera_url": "rtsp://example.com/stream",
                "change": "camera_add",
                "metadata": {
                    "prompt": "Describe what you see",
                    "model": "test-model",
                    "chunk_duration": 10,
                    "stream": True,
                },
            },
        }

        first_response = client.post(f"{API_PREFIX}/stream/add", json=body)
        assert first_response.status_code == 200
        assert first_response.json()["status"] == "processing"
        assert first_response.json()["inference"] is True

        duplicate_response = client.post(f"{API_PREFIX}/stream/add", json=body)
        assert duplicate_response.status_code == 409
        assert duplicate_response.json()["code"] == "DuplicateCameraId"
        assert rtvi_server._process_vlm_request.await_count == 1


class TestNIMCompatibleEndpoints:
    """Test NIM-compatible endpoints"""

    def test_temporary_chat_asset_cleanup_unregisters_and_deduplicates(self, rtvi_server, tmp_path):
        """Temporary chat assets are removed from handler and AssetManager exactly once."""
        media_path = tmp_path / "clip.mp4"
        media_path.write_bytes(b"test-video")
        asset_id = rtvi_server._asset_manager.add_file(
            str(media_path),
            "vision",
            "video",
        )
        asset = rtvi_server._asset_manager.get_asset(asset_id)
        rtvi_server._stream_handler.remove_video_file = MagicMock()
        rtvi_server._asset_manager.cleanup_asset = MagicMock()

        asyncio.run(rtvi_server._cleanup_temporary_chat_assets([asset_id, asset_id]))

        rtvi_server._stream_handler.remove_video_file.assert_called_once_with(asset)
        rtvi_server._asset_manager.cleanup_asset.assert_called_once()
        cleanup_call = rtvi_server._asset_manager.cleanup_asset.call_args
        assert cleanup_call.args == (asset_id,)
        assert cleanup_call.kwargs == {"executor": rtvi_server._cleanup_executor}

    def test_stream_close_cleanup_continues_after_cancellation(self, rtvi_server):
        """Client disconnect cancellation must not cancel temporary asset cleanup."""

        async def run_cleanup_cancel_test():
            cleanup_started = asyncio.Event()
            cleanup_can_finish = asyncio.Event()
            cleaned_asset_ids = []

            async def cleanup_assets(asset_ids):
                cleanup_started.set()
                await cleanup_can_finish.wait()
                cleaned_asset_ids.extend(asset_ids)

            rtvi_server._cleanup_temporary_chat_assets = cleanup_assets

            cleanup_waiter = asyncio.create_task(
                rtvi_server._cleanup_temporary_chat_assets_after_stream_close(["temp-asset"])
            )
            await cleanup_started.wait()
            cleanup_waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cleanup_waiter

            cleanup_tasks = list(rtvi_server._temporary_chat_asset_cleanup_tasks)
            assert len(cleanup_tasks) == 1
            cleanup_can_finish.set()
            await asyncio.wait_for(asyncio.gather(*cleanup_tasks), timeout=1)
            assert cleaned_asset_ids == ["temp-asset"]
            assert not rtvi_server._temporary_chat_asset_cleanup_tasks

        asyncio.run(run_cleanup_cancel_test())

    def test_chat_completions_file_url_temp_asset_cleanup_on_non_stream_failure(
        self, test_client, rtvi_server, tmp_path, monkeypatch
    ):
        """Internally-created file URL assets are reclaimed when post-enqueue work fails."""
        monkeypatch.setenv("RTVI_ALLOWED_LOCAL_MEDIA_PATHS", str(tmp_path))
        media_path = tmp_path / "clip.mp4"
        media_path.write_bytes(b"test-video")
        created_asset_ids = []

        async def process_request(vlm_query, video_id_list, log_prefix, is_chat_completion=False):
            del vlm_query, log_prefix
            assert is_chat_completion is True
            created_asset_ids.extend(video_id_list)
            asset = rtvi_server._asset_manager.get_asset(video_id_list[0])
            return str(uuid.uuid4()), asset, [asset]

        rtvi_server._process_vlm_request = AsyncMock(side_effect=process_request)
        rtvi_server._stream_handler.wait_for_request_done = MagicMock(
            side_effect=RuntimeError("forced wait failure")
        )
        rtvi_server._stream_handler.remove_video_file = MagicMock()
        rtvi_server._asset_manager.cleanup_asset = MagicMock()

        response = test_client.post(
            f"{API_PREFIX}/chat/completions",
            json={
                "model": "test-model",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe this video."},
                            {
                                "type": "video_url",
                                "video_url": {"url": f"file://{media_path}"},
                            },
                        ],
                    }
                ],
            },
        )

        assert response.status_code == 500
        assert len(created_asset_ids) == 1
        created_asset = rtvi_server._asset_manager.get_asset(created_asset_ids[0])
        rtvi_server._stream_handler.remove_video_file.assert_called_once_with(created_asset)
        rtvi_server._asset_manager.cleanup_asset.assert_called_once()
        cleanup_call = rtvi_server._asset_manager.cleanup_asset.call_args
        assert cleanup_call.args == (created_asset_ids[0],)
        assert cleanup_call.kwargs == {"executor": rtvi_server._cleanup_executor}

    def test_chat_completions_file_url_temp_asset_cleanup_on_media_kwargs_error(
        self, test_client, rtvi_server, tmp_path, monkeypatch
    ):
        """Invalid query parameters after temporary asset creation still reclaim the asset."""
        monkeypatch.setenv("RTVI_ALLOWED_LOCAL_MEDIA_PATHS", str(tmp_path))
        media_path = tmp_path / "clip.mp4"
        media_path.write_bytes(b"test-video")
        rtvi_server._stream_handler.remove_video_file = MagicMock()
        rtvi_server._asset_manager.cleanup_asset = MagicMock()

        response = test_client.post(
            f"{API_PREFIX}/chat/completions",
            json={
                "model": "test-model",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe this video."},
                            {
                                "type": "video_url",
                                "video_url": {"url": f"file://{media_path}"},
                            },
                        ],
                    }
                ],
                "media_io_kwargs": {"video": {"fps": "bad"}},
            },
        )

        assert response.status_code == 400
        rtvi_server._stream_handler.remove_video_file.assert_called_once()
        rtvi_server._asset_manager.cleanup_asset.assert_called_once()

    def test_chat_completions_explicit_id_failure_does_not_cleanup_uploaded_asset(
        self, test_client, rtvi_server, tmp_path
    ):
        """Assets supplied through request_body.id remain caller-owned."""
        media_path = tmp_path / "uploaded.mp4"
        media_path.write_bytes(b"test-video")
        asset_id = rtvi_server._asset_manager.add_file(str(media_path), "vision", "video")
        asset = rtvi_server._asset_manager.get_asset(asset_id)

        async def process_request(vlm_query, video_id_list, log_prefix, is_chat_completion=False):
            del vlm_query, video_id_list, log_prefix
            assert is_chat_completion is True
            return str(uuid.uuid4()), asset, [asset]

        rtvi_server._process_vlm_request = AsyncMock(side_effect=process_request)
        rtvi_server._stream_handler.wait_for_request_done = MagicMock(
            side_effect=RuntimeError("forced wait failure")
        )
        rtvi_server._stream_handler.remove_video_file = MagicMock()
        rtvi_server._asset_manager.cleanup_asset = MagicMock()

        response = test_client.post(
            f"{API_PREFIX}/chat/completions",
            json={
                "model": "test-model",
                "id": asset_id,
                "messages": [{"role": "user", "content": "Describe this video."}],
            },
        )

        assert response.status_code == 500
        rtvi_server._stream_handler.remove_video_file.assert_not_called()
        rtvi_server._asset_manager.cleanup_asset.assert_not_called()
        assert rtvi_server._asset_manager.get_asset(asset_id) is asset

    def test_chat_completions_file_url_stream_cleanup_after_done(
        self, test_client, rtvi_server, tmp_path, monkeypatch
    ):
        """Streaming chat URL assets are reclaimed after terminal SSE output."""
        monkeypatch.setenv("RTVI_ALLOWED_LOCAL_MEDIA_PATHS", str(tmp_path))
        media_path = tmp_path / "clip.mp4"
        media_path.write_bytes(b"test-video")
        request_id = str(uuid.uuid4())
        created_asset_ids = []

        async def process_request(vlm_query, video_id_list, log_prefix, is_chat_completion=False):
            del vlm_query, log_prefix
            assert is_chat_completion is True
            created_asset_ids.extend(video_id_list)
            asset = rtvi_server._asset_manager.get_asset(video_id_list[0])
            req_info = RequestInfo()
            req_info.request_id = request_id
            req_info.status = RequestInfo.Status.SUCCESSFUL
            req_info.queue_time = time.time()
            req_info.assets = [asset]
            req_info.is_live = False
            rtvi_server._stream_handler._request_info_map[request_id] = req_info
            return request_id, asset, [asset]

        rtvi_server._process_vlm_request = AsyncMock(side_effect=process_request)
        rtvi_server._stream_handler.get_response = MagicMock(
            side_effect=lambda *_args, **_kwargs: (
                rtvi_server._stream_handler._request_info_map[request_id],
                [],
            )
        )
        rtvi_server._stream_handler.remove_video_file = MagicMock()
        rtvi_server._asset_manager.cleanup_asset = MagicMock()

        with test_client.stream(
            "POST",
            f"{API_PREFIX}/chat/completions",
            json={
                "model": "test-model",
                "stream": True,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe this video."},
                            {
                                "type": "video_url",
                                "video_url": {"url": f"file://{media_path}"},
                            },
                        ],
                    }
                ],
            },
        ) as response:
            body = "".join(response.iter_text())

        assert response.status_code == 200
        assert "[DONE]" in body
        assert len(created_asset_ids) == 1
        created_asset = rtvi_server._asset_manager.get_asset(created_asset_ids[0])
        rtvi_server._stream_handler.remove_video_file.assert_called_once_with(created_asset)
        rtvi_server._asset_manager.cleanup_asset.assert_called_once()

    def test_chat_completions_text_only_preserves_reasoning_envelope(
        self, test_client, rtvi_server
    ):
        """chat/completions passes through raw CR2 reasoning tags from model adapters."""
        pipeline = rtvi_server._stream_handler._vlm_pipeline
        model_output = "\n".join(
            [
                "<think>",
                "2 + 2 is basic addition.",
                "</think>",
                "",
                "<answer>",
                "4",
                "</answer>",
            ]
        )

        def enqueue_text_chunk(**kwargs):
            assert kwargs["vlm_query"].preserve_reasoning_tags is True
            vlm_output = MagicMock()
            vlm_output.output = model_output
            vlm_output.reasoning_description = ""
            vlm_output.input_tokens = 52
            vlm_output.output_tokens = 154
            kwargs["on_chunk_result"](MagicMock(vlm_model_output=vlm_output))

        pipeline.enqueue_vlm_text_chunk.side_effect = enqueue_text_chunk

        response = test_client.post(
            f"{API_PREFIX}/chat/completions",
            json={
                "model": "test-model",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "What is 2 + 2? Answer the question in the following format: "
                            "<think>\nyour reasoning\n</think>\n\n<answer>\nyour answer\n</answer>."
                        ),
                    }
                ],
                "max_tokens": 512,
            },
        )

        assert response.status_code == 200
        content = response.json()["choices"][0]["message"]["content"]
        assert content == model_output

    def test_get_version(self, test_client):
        """Test version endpoint"""
        response = test_client.get(f"{API_PREFIX}/version")
        assert response.status_code == 200
        data = response.json()
        assert "release" in data
        assert "api" in data

    def test_get_manifest(self, test_client):
        """Test manifest endpoint"""
        response = test_client.get(f"{API_PREFIX}/manifest")
        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert "model" in data

    def test_health_live_nim(self, test_client):
        """Test NIM-compatible liveness endpoint"""
        response = test_client.get(f"{API_PREFIX}/health/live")
        assert response.status_code in [200, 503]  # Can be healthy or unhealthy
        data = response.json()
        assert "object" in data
        assert "message" in data

    def test_health_ready_nim(self, test_client):
        """Test NIM-compatible readiness endpoint"""
        response = test_client.get(f"{API_PREFIX}/health/ready")
        assert response.status_code in [200, 503]  # Can be healthy or unhealthy
        data = response.json()
        assert "object" in data
        assert "message" in data

    def test_chat_completions_text_only(self, test_client):
        """Test text-only chat completions (no file ID, no media URL)."""
        response = test_client.post(
            f"{API_PREFIX}/chat/completions",
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Test"}],
            },
        )
        # Text-only request routes through VLM pipeline.
        # In test env (no model), it times out (504) or succeeds (200).
        assert response.status_code in (200, 504)

    def test_chat_completions_missing_messages(self, test_client):
        """Test chat completions without messages"""
        fake_id = str(uuid.uuid4())
        response = test_client.post(
            f"{API_PREFIX}/chat/completions",
            json={"model": "test-model", "id": fake_id},
        )
        assert response.status_code == 422  # Validation error

    def test_chat_completions_invalid_model(self, test_client):
        """Test chat completions with invalid model"""
        fake_id = str(uuid.uuid4())
        response = test_client.post(
            f"{API_PREFIX}/chat/completions",
            json={
                "model": "invalid-model",
                "messages": [{"role": "user", "content": "Test"}],
                "id": fake_id,
            },
        )
        assert response.status_code == 400  # Invalid model

    def test_completions_endpoint(self, test_client):
        """Test completions endpoint (should return error for VLM)"""
        response = test_client.post(
            f"{API_PREFIX}/completions",
            json={"model": "test-model", "prompt": "Complete this"},
        )
        # Should return error explaining VLM requires video/image
        assert response.status_code in [400, 501]


class TestIntegrationWithServer:
    """Integration tests with actual server instance"""

    @pytest.mark.skipif(
        os.getenv("SKIP_INTEGRATION_TESTS") == "1", reason="Integration tests disabled"
    )
    def test_server_startup_shutdown(self, mock_args):
        """Test server can start and stop"""
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "MESSAGE_BUS": ""}):
            # Mock VlmPipeline to avoid hanging on GPU initialization
            with patch("server.rtvi_stream_handler.VlmPipeline") as mock_vlm_pipeline_class:
                mock_pipeline = MagicMock()
                mock_pipeline.get_models_info.return_value = {"object": "list", "data": []}
                mock_vlm_pipeline_class.return_value = mock_pipeline
                server = RTVIServer(mock_args)
                # Note: Full server.run() would block, so we just test initialization
                assert server._app is not None
                if hasattr(server, "_stream_handler") and server._stream_handler:
                    server._stream_handler.stop()

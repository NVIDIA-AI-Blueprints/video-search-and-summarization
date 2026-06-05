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
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api_models.captions import VlmQuery
from common.chunk_info import ChunkInfo
from models.base_vlm_model import VlmModelOutput
from server.rtvi_stream_handler import RequestInfo
from server.rtvi_vlm_server import RTVIServer, _build_chat_assistant_message
from tests.tests_common import TempEnv
from vlm_pipeline.vlm_pipeline import PipelineChunkResult, VlmModelType

API_PREFIX = "/v1"


class TestChatCompletionFormatting:
    """Test chat completion response formatting helpers."""

    def test_assistant_message_includes_think_tags_when_reasoning_is_present(self):
        message = _build_chat_assistant_message("final answer", "parsed reasoning")

        assert message.content == "<think>\nparsed reasoning\n</think>\n\nfinal answer"
        assert message.reasoning_description == "parsed reasoning"

    def test_assistant_message_allows_reasoning_only_response(self):
        message = _build_chat_assistant_message("", "parsed reasoning")

        assert message.content == "<think>\nparsed reasoning\n</think>"
        assert message.reasoning_description == "parsed reasoning"


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
    args.kafka_enabled = False
    args.kafka_topic = "mdx-vlm-captions"
    args.kafka_bootstrap_servers = ""
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
    with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "KAFKA_ENABLED": "false"}):
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

    def test_vlm_server_rtsp_shim_uses_independent_pipeline_streams(self, rtvi_server):
        """The VLM server shim gives each caption request a private pipeline ID."""
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
        assert {req.pipeline_stream_id for req in live_requests} == {
            first_request_id,
            second_request_id,
        }

        pipeline_assets = [
            call.kwargs["asset"]
            for call in rtvi_server._stream_handler._vlm_pipeline.add_live_stream.call_args_list
        ]
        assert [pipeline_asset.asset_id for pipeline_asset in pipeline_assets] == [
            first_request_id,
            second_request_id,
        ]
        assert all(pipeline_asset.path == asset.path for pipeline_asset in pipeline_assets)

    def test_vlm_server_rtsp_shim_removes_all_pipeline_streams_for_asset(self, rtvi_server):
        """Deleting an added stream drains every caption request created by the shim."""
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

        rtvi_server._stream_handler.remove_rtsp_stream(asset)

        assert asset.use_count == 0
        assert first_request_id not in rtvi_server._stream_handler._request_info_map
        assert second_request_id not in rtvi_server._stream_handler._request_info_map
        assert [
            call.args[0]
            for call in rtvi_server._stream_handler._vlm_pipeline.remove_live_stream.call_args_list
        ] == [first_request_id, second_request_id]
        removed_frame_dirs = [
            call.args[0] for call in rtvi_server._stream_handler._safe_rmtree.call_args_list
        ]
        assert removed_frame_dirs == [
            os.path.join(tempfile.gettempdir(), "rtvi", "cached_frames", first_request_id),
            os.path.join(tempfile.gettempdir(), "rtvi", "cached_frames", second_request_id),
        ]

    def test_vlm_server_rtsp_shim_reports_original_stream_id(self, rtvi_server):
        """Private pipeline IDs must not leak into live caption chunks."""
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
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "KAFKA_ENABLED": "false"}):
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

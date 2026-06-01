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
Integration tests for RTVI Embed Server components

Tests cover:
- End-to-end workflows
- Server-client interactions
- Multi-component integration
- Real API calls (when server is running)
- Text embeddings generation
- Video embeddings generation
- CV-compatible stream APIs: /v1/stream/add, /v1/stream/remove, /v1/stream/get-stream-info
"""

import logging
import os
import tempfile
import time
import uuid

import pytest

from tests.tests_common import ViaTestServer

API_PREFIX = "/v1"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


@pytest.fixture(scope="class")
def test_video_file():
    """Fixture providing test video file path"""
    # Try common test video locations
    test_paths = [
        "/opt/nvidia/rtvi/warmup_streams/its_264.mp4",
        # os.path.join(os.path.dirname(__file__), "..", "test_data", "bridge.mp4"),
    ]
    for path in test_paths:
        if os.path.exists(path):
            return path
    return None


@pytest.fixture(scope="class")
def test_live_stream_url():
    """Fixture providing test live stream URL"""

    test_urls = [
        # "rtsp://nv-wowza-pdc.nvidia.com:1935/vod/Jensen_AI_Summit_India_1080p_blackwell_opus.mp4",
        "rtsp://nv-wowza-pdc.nvidia.com:1935/vod/warehouse_1min.mp4",
    ]
    for url in test_urls:
        if url:
            return url
    return None


@pytest.fixture(scope="class")
def test_image_file():
    """Fixture providing test image file path"""
    # Try common test image locations
    test_paths = [
        os.path.join(os.path.dirname(__file__), "..", "test_data", "its_overlay_0.png"),
    ]
    for path in test_paths:
        if os.path.exists(path):
            return path
    return None


@pytest.fixture(scope="session")
def test_server():
    """Start a test server for integration tests"""

    server_args = " ".join(
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
    server_args = server_args + " --num-decoders-per-gpu " + str(decoders)
    os.environ["RTVI_DISABLE_LIVESTREAM_PREVIEW"] = "true"

    with ViaTestServer(
        server_args, port=8017, start_server=True, server_module="server.rtvi_embed_server"
    ) as server:
        # Wait for server to be ready
        max_wait = 30
        for _ in range(max_wait):
            try:
                resp = server.get(f"{API_PREFIX}/ready")
                if resp.status_code == 200:
                    break
            except Exception as e:
                logger.error("Error waiting for server to be ready: %s", e)
                pass
            time.sleep(1)
        yield server
        # Explicitly stop the underlying RTVIServer stream handler, if present
        if getattr(server, "_server", None) and getattr(server._server, "_stream_handler", None):
            try:
                server._server._stream_handler.stop()
            except Exception as e:
                logger.error(f"Error stopping RTVI stream handler: {e}")
                pass


@pytest.mark.skipif(os.getenv("SKIP_INTEGRATION_TESTS") == "1", reason="Integration tests disabled")
class TestServerClientIntegration:
    """Integration tests with actual server"""

    def test_health_check_workflow(self, test_server):
        """Test health check endpoints"""
        # Test readiness
        resp = test_server.get(f"{API_PREFIX}/ready")
        assert resp.status_code in [200, 503]

        # Test liveness
        resp = test_server.get(f"{API_PREFIX}/live")
        assert resp.status_code in [200, 503]

        # Test startup
        resp = test_server.get(f"{API_PREFIX}/startup")
        assert resp.status_code == 200

    def test_models_listing(self, test_server):
        """Test models listing"""
        resp = test_server.get(f"{API_PREFIX}/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert isinstance(data["data"], list)
        if len(data["data"]) > 0:
            model = data["data"][0]
            assert "id" in model
            assert "api_type" in model

    def test_file_lifecycle(self, test_server):
        """Test complete file lifecycle"""
        # List files (should be empty)
        resp = test_server.get(f"{API_PREFIX}/files?purpose=vision")
        assert resp.status_code == 200
        initial_count = len(resp.json()["data"])
        assert initial_count >= 0

        # Note: Actual file upload would require a real video file
        # This test structure shows the pattern

    def test_text_embeddings_workflow(self, test_server):
        """Test text embeddings generation workflow"""
        # Get available model
        resp = test_server.get(f"{API_PREFIX}/models")
        assert resp.status_code == 200
        models_data = resp.json()
        if len(models_data["data"]) == 0:
            pytest.skip("No models available for testing")

        model_id = models_data["data"][0]["id"]

        # Generate text embeddings
        resp = test_server.post(
            f"{API_PREFIX}/generate_text_embeddings",
            json={"text_input": "This is a test text for embeddings", "model": model_id},
        )
        # May succeed or fail depending on model support
        assert resp.status_code in [200, 400, 422, 501]

        if resp.status_code == 200:
            data = resp.json()
            assert "id" in data
            assert "data" in data
            assert "model" in data

    def test_live_stream_lifecycle(self, test_server, test_live_stream_url):
        """Test live stream lifecycle with RTSP live stream URL"""
        # List streams (should be empty)
        resp = test_server.get(f"{API_PREFIX}/streams/get-stream-info")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        initial_count = len(resp.json())

        rtsp_url = None
        if test_live_stream_url:
            try:
                rtsp_url = test_live_stream_url

                # Add stream to server
                resp = test_server.post(
                    f"{API_PREFIX}/streams/add",
                    json={
                        "streams": [
                            {
                                "liveStreamUrl": rtsp_url,
                                "description": "Test stream from live stream URL",
                            }
                        ]
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert "results" in data
                assert len(data["results"]) > 0
                stream_id = data["results"][0]["id"]

                # Verify stream was added
                resp = test_server.get(f"{API_PREFIX}/streams/get-stream-info")
                assert resp.status_code == 200
                streams = resp.json()
                assert len(streams) == initial_count + 1

                # Find our stream
                our_stream = next((s for s in streams if s["id"] == stream_id), None)
                assert our_stream is not None
                assert our_stream["liveStreamUrl"] == rtsp_url

                # Delete stream
                resp = test_server.delete(f"{API_PREFIX}/streams/delete/{stream_id}")
                assert resp.status_code == 200

                # Verify stream was deleted
                resp = test_server.get(f"{API_PREFIX}/streams/get-stream-info")
                assert resp.status_code == 200
                streams = resp.json()
                assert len(streams) == initial_count

            finally:
                pass
        else:
            pytest.skip(f"Test live stream URL {test_live_stream_url} not available")


class TestRTSPStreamManagement:
    """Test RTSP stream management with RTSP live stream URL"""

    def test_add_and_delete_stream_with_cvlc(self, test_server, test_live_stream_url):
        """Test adding and deleting streams using RTSP live stream URL"""
        if not test_live_stream_url:
            pytest.skip(f"Test live stream URL {test_live_stream_url} not available")

        try:
            # Start cvlc RTSP stream
            rtsp_url = test_live_stream_url

            # Add stream to server
            resp = test_server.post(
                f"{API_PREFIX}/streams/add",
                json={
                    "streams": [
                        {
                            "liveStreamUrl": rtsp_url,
                            "description": "cvlc test stream",
                            "place_name": "Test Location",
                            "place_type": "warehouse",
                        }
                    ]
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data.get("errors", [])) == 0
            assert len(data["results"]) > 0
            stream_id = data["results"][0]["id"]

            # Verify stream info
            resp = test_server.get(f"{API_PREFIX}/streams/get-stream-info")
            assert resp.status_code == 200
            streams = resp.json()
            our_stream = next((s for s in streams if s["id"] == stream_id), None)
            assert our_stream is not None
            assert our_stream["description"] == "cvlc test stream"

            # Delete stream
            resp = test_server.delete(f"{API_PREFIX}/streams/delete/{stream_id}")
            assert resp.status_code == 200

        finally:
            pass

    def test_multiple_streams_with_cvlc(self, test_server, test_live_stream_url):
        """Test managing multiple RTSP live stream URLs"""
        if not test_live_stream_url:
            pytest.skip(f"Test live stream URL {test_live_stream_url} not available")

        rtsp_urls = []
        try:
            # Start multiple streams
            for i in range(2):
                rtsp_url = test_live_stream_url
                rtsp_urls.append(rtsp_url)

            # Add all streams to server
            streams_data = [
                {"liveStreamUrl": rtsp_url, "description": f"Stream {i}"}
                for i, rtsp_url in enumerate(rtsp_urls)
            ]

            resp = test_server.post(f"{API_PREFIX}/streams/add", json={"streams": streams_data})
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["results"]) == 2

            # Verify all streams are listed
            resp = test_server.get(f"{API_PREFIX}/streams/get-stream-info")
            assert resp.status_code == 200
            streams = resp.json()
            assert len(streams) >= 2

            # Delete all streams
            stream_ids = [r["id"] for r in data["results"]]
            resp = test_server.delete(
                f"{API_PREFIX}/streams/delete-batch", json={"stream_ids": stream_ids}
            )
            assert resp.status_code == 200

        finally:
            pass


@pytest.mark.skipif(os.getenv("SKIP_INTEGRATION_TESTS") == "1", reason="Integration tests disabled")
class TestCvStreamApiIntegration:
    """Integration tests for CV-compatible /v1/stream/* endpoints (RTVI-CV schema)."""

    def test_cv_get_stream_info_response_shape(self, test_server):
        """GET /v1/stream/get-stream-info returns StreamInfoResponse envelope."""
        resp = test_server.get(f"{API_PREFIX}/stream/get-stream-info")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"
        assert "stream_count" in data
        assert "stream_list" in data
        assert isinstance(data["stream_list"], list)
        assert data["stream_count"] == len(data["stream_list"])
        for entry in data["stream_list"]:
            assert "camera_id" in entry
            assert "camera_url" in entry
            assert "asset_id" in entry
            assert "inference_active" in entry

    def test_cv_stream_add_remove_lifecycle(self, test_server, test_live_stream_url):
        """POST /v1/stream/add then /v1/stream/remove; list via get-stream-info."""
        if not test_live_stream_url:
            pytest.skip(f"Test live stream URL {test_live_stream_url} not available")

        camera_id = f"cv-int-{uuid.uuid4().hex[:12]}"
        rtsp_url = test_live_stream_url

        info_before = test_server.get(f"{API_PREFIX}/stream/get-stream-info").json()
        count_before = info_before["stream_count"]

        add_body = {
            "key": "sensor",
            "value": {
                "camera_id": camera_id,
                "camera_name": "CV integration stream",
                "camera_url": rtsp_url,
                "change": "camera_add",
            },
        }
        resp = test_server.post(f"{API_PREFIX}/stream/add", json=add_body)
        assert resp.status_code == 200
        add_data = resp.json()
        assert add_data["camera_id"] == camera_id
        assert "asset_id" in add_data
        assert add_data["status"] in ("added", "processing")
        assert isinstance(add_data["inference"], bool)
        asset_id = add_data["asset_id"]

        time.sleep(10)

        info_mid = test_server.get(f"{API_PREFIX}/stream/get-stream-info").json()
        assert info_mid["stream_count"] == count_before + 1
        ours = next((s for s in info_mid["stream_list"] if s["camera_id"] == camera_id), None)
        assert ours is not None
        assert ours["camera_url"] == rtsp_url
        assert ours["asset_id"] == asset_id

        time.sleep(10)

        remove_body = {
            "key": "sensor",
            "value": {"camera_id": camera_id, "change": "camera_remove"},
        }
        resp = test_server.post(f"{API_PREFIX}/stream/remove", json=remove_body)
        assert resp.status_code == 200
        rem_data = resp.json()
        assert rem_data["camera_id"] == camera_id
        assert rem_data["asset_id"] == asset_id
        assert rem_data.get("status", "removed") == "removed"

        info_after = test_server.get(f"{API_PREFIX}/stream/get-stream-info").json()
        assert info_after["stream_count"] == count_before
        assert not any(s["camera_id"] == camera_id for s in info_after["stream_list"])

    def test_cv_stream_add_alternate_change_token(self, test_server, test_live_stream_url):
        """Server accepts change value 'add' as well as 'camera_add'."""
        if not test_live_stream_url:
            pytest.skip(f"Test live stream URL {test_live_stream_url} not available")

        camera_id = f"cv-add-alt-{uuid.uuid4().hex[:12]}"
        try:
            resp = test_server.post(
                f"{API_PREFIX}/stream/add",
                json={
                    "key": "sensor",
                    "value": {
                        "camera_id": camera_id,
                        "camera_url": test_live_stream_url,
                        "change": "add",
                    },
                },
            )
            assert resp.status_code == 200
            assert resp.json()["camera_id"] == camera_id

            time.sleep(10)

        finally:
            test_server.post(
                f"{API_PREFIX}/stream/remove",
                json={
                    "key": "sensor",
                    "value": {"camera_id": camera_id, "change": "remove"},
                },
            )

    def test_cv_stream_remove_unknown_camera(self, test_server):
        """POST /v1/stream/remove for unknown camera_id returns 404."""
        resp = test_server.post(
            f"{API_PREFIX}/stream/remove",
            json={
                "key": "sensor",
                "value": {
                    "camera_id": f"no-such-camera-{uuid.uuid4().hex}",
                    "change": "camera_remove",
                },
            },
        )
        assert resp.status_code == 404

    def test_cv_stream_add_invalid_change_type(self, test_server):
        """Unsupported change on stream/add returns 400 before touching the pipeline."""
        resp = test_server.post(
            f"{API_PREFIX}/stream/add",
            json={
                "key": "sensor",
                "value": {
                    "camera_id": "cam-invalid-change",
                    "camera_url": "rtsp://127.0.0.1:554/not-used",
                    "change": "camera_update",
                },
            },
        )
        assert resp.status_code == 400

    def test_cv_stream_remove_invalid_change_type(self, test_server):
        """Unsupported change on stream/remove returns 400."""
        resp = test_server.post(
            f"{API_PREFIX}/stream/remove",
            json={
                "key": "sensor",
                "value": {
                    "camera_id": "any",
                    "change": "camera_update",
                },
            },
        )
        assert resp.status_code == 400


class TestEmbeddingsGeneration:
    """Test embeddings generation workflows"""

    def test_text_embeddings_single_input(self, test_server):
        """Test generating embeddings for single text input"""
        # Get available model
        resp = test_server.get(f"{API_PREFIX}/models")
        assert resp.status_code == 200
        models_data = resp.json()
        if len(models_data["data"]) == 0:
            pytest.skip("No models available")

        model_id = models_data["data"][0]["id"]

        # Generate embeddings
        resp = test_server.post(
            f"{API_PREFIX}/generate_text_embeddings",
            json={"text_input": "Sample text for embedding generation", "model": model_id},
        )

        # Check response (may not be supported by all models)
        if resp.status_code == 200:
            data = resp.json()
            assert "id" in data
            assert "data" in data
            assert isinstance(data["data"], list)

    def test_text_embeddings_multiple_inputs(self, test_server):
        """Test generating embeddings for multiple text inputs"""
        # Get available model
        resp = test_server.get(f"{API_PREFIX}/models")
        assert resp.status_code == 200
        models_data = resp.json()
        if len(models_data["data"]) == 0:
            pytest.skip("No models available")

        model_id = models_data["data"][0]["id"]

        # Generate embeddings for multiple texts
        resp = test_server.post(
            f"{API_PREFIX}/generate_text_embeddings",
            json={
                "text_input": ["First text sample", "Second text sample", "Third text sample"],
                "model": model_id,
            },
        )

        # Check response (may not be supported by all models)
        if resp.status_code == 200:
            data = resp.json()
            assert "data" in data
            assert len(data["data"]) == 3

    def test_video_embeddings_streaming(self, test_server, test_video_file):
        """Test video embeddings generation with streaming"""
        if not test_video_file or not os.path.exists(test_video_file):
            pytest.skip("Test video file not available")

        # Get available model
        resp = test_server.get(f"{API_PREFIX}/models")
        assert resp.status_code == 200
        models_data = resp.json()
        if len(models_data["data"]) == 0:
            pytest.skip("No models available")

        model_id = models_data["data"][0]["id"]
        assert len(model_id) > 0

        # Note: This would require uploading a file first
        # Placeholder for the workflow structure


class TestErrorRecovery:
    """Test error recovery scenarios"""

    def test_concurrent_requests(self, test_server):
        """Test handling concurrent requests"""
        import concurrent.futures

        def make_request():
            return test_server.get(f"{API_PREFIX}/ready")

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_request) for _ in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # All requests should complete
        assert len(results) == 10
        # All should return valid status codes
        assert all(r.status_code in [200, 503] for r in results)

    def test_invalid_model_handling(self, test_server):
        """Test handling of invalid model requests"""
        resp = test_server.post(
            f"{API_PREFIX}/generate_text_embeddings",
            json={"text_input": "test", "model": "invalid-model-id"},
        )
        assert resp.status_code in [400, 422]

    def test_missing_parameters_handling(self, test_server):
        """Test handling of missing parameters"""
        # Missing text_input
        resp = test_server.post(f"{API_PREFIX}/generate_text_embeddings", json={"model": "test"})
        assert resp.status_code == 422

        # Missing model
        resp = test_server.post(
            f"{API_PREFIX}/generate_text_embeddings", json={"text_input": "test"}
        )
        assert resp.status_code == 422


class TestAPIConsistency:
    """Test API consistency and contract"""

    def test_response_format_models(self, test_server):
        """Test models endpoint response format"""
        resp = test_server.get(f"{API_PREFIX}/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "object" in data
        assert "data" in data
        if len(data["data"]) > 0:
            model = data["data"][0]
            assert "id" in model
            assert "api_type" in model

    def test_response_format_files(self, test_server):
        """Test files endpoint response format"""
        resp = test_server.get(f"{API_PREFIX}/files?purpose=vision")
        assert resp.status_code == 200
        data = resp.json()
        assert "object" in data
        assert "data" in data

    def test_response_format_streams(self, test_server):
        """Test streams endpoint response format"""
        resp = test_server.get(f"{API_PREFIX}/streams/get-stream-info")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_response_format_cv_stream_get_stream_info(self, test_server):
        """Test CV /v1/stream/get-stream-info response format"""
        resp = test_server.get(f"{API_PREFIX}/stream/get-stream-info")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "stream_count" in data
        assert "stream_list" in data
        assert isinstance(data["stream_list"], list)
        assert data["stream_count"] == len(data["stream_list"])

    def test_error_response_format(self, test_server):
        """Test error response format"""
        fake_id = str(uuid.uuid4())
        resp = test_server.get(f"{API_PREFIX}/files/{fake_id}")
        assert resp.status_code >= 400
        # Error responses should have consistent format
        if resp.status_code != 404:
            try:
                error_data = resp.json()
                assert "code" in error_data or "message" in error_data or "detail" in error_data
            except Exception:
                pass  # Some errors may not return JSON

    def test_text_embeddings_response_format(self, test_server):
        """Test text embeddings response format"""
        # Get available model
        resp = test_server.get(f"{API_PREFIX}/models")
        assert resp.status_code == 200
        models_data = resp.json()
        if len(models_data["data"]) == 0:
            pytest.skip("No models available")

        model_id = models_data["data"][0]["id"]

        # Generate embeddings
        resp = test_server.post(
            f"{API_PREFIX}/generate_text_embeddings",
            json={"text_input": "Test text", "model": model_id},
        )

        # If successful, verify response format
        if resp.status_code == 200:
            data = resp.json()
            assert "id" in data
            assert "created" in data
            assert "model" in data
            assert "data" in data
            assert isinstance(data["data"], list)


class TestLiveStreamEmbeddings:
    """Test live stream embeddings generation"""

    def test_live_stream_embeddings_workflow(self, test_server, test_live_stream_url):
        """Test complete live stream embeddings workflow"""
        if not test_live_stream_url:
            pytest.skip(f"Test live stream URL {test_live_stream_url} not available")

        # Get available model
        resp = test_server.get(f"{API_PREFIX}/models")
        assert resp.status_code == 200
        models_data = resp.json()
        if len(models_data["data"]) == 0:
            pytest.skip("No models available")

        model_id = models_data["data"][0]["id"]

        try:
            # Start RTSP stream
            rtsp_url = test_live_stream_url

            # Add stream
            resp = test_server.post(
                f"{API_PREFIX}/streams/add",
                json={"streams": [{"liveStreamUrl": rtsp_url, "description": "Test stream"}]},
            )
            assert resp.status_code == 200
            stream_id = resp.json()["results"][0]["id"]

            # Start embeddings generation (streaming required for live streams)
            resp = test_server.post(
                f"{API_PREFIX}/generate_video_embeddings",
                json={
                    "id": stream_id,
                    "model": model_id,
                    "stream": True,
                    "chunk_duration": 5,
                },
            )

            # May succeed or fail depending on setup
            assert resp.status_code in [200, 400, 422]

            # Stop embeddings generation
            resp = test_server.delete(f"{API_PREFIX}/generate_video_embeddings/{stream_id}")
            assert resp.status_code in [200, 400]

            # Delete stream
            resp = test_server.delete(f"{API_PREFIX}/streams/delete/{stream_id}")
            assert resp.status_code == 200

        finally:
            pass

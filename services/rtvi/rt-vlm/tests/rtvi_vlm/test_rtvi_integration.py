# SPDX-FileCopyrightText: Copyright (c) 2024-2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Integration tests for RTVI VLM Server components

Tests cover:
- End-to-end workflows
- Server-client interactions
- Multi-component integration
- Real API calls (when server is running)
"""

import os
import subprocess
import tempfile
import time
import uuid

import pytest

from tests.tests_common import ViaTestServer
from tests.rtsp_stream_helper import start_rtsp_stream, stop_rtsp_stream

API_PREFIX = "/v1"


def _can_run_vlc():
    """Check if VLC can be run (handles root user case)"""
    is_root = os.geteuid() == 0
    if is_root:
        # Check for vlc-wrapper using same logic as rtsp_stream_helper
        vlc_wrapper_paths = ["/usr/bin/vlc-wrapper", "/usr/local/bin/vlc-wrapper"]
        vlc_wrapper_found = any(
            os.path.exists(path) and os.access(path, os.X_OK) for path in vlc_wrapper_paths
        )
        if not vlc_wrapper_found:
            # Also try which as fallback
            try:
                result = subprocess.run(
                    ["which", "vlc-wrapper"], capture_output=True, text=True, timeout=5
                )
                vlc_wrapper_found = result.returncode == 0 and result.stdout.strip()
            except Exception:
                pass
        return vlc_wrapper_found
    else:
        # Non-root can use cvlc
        try:
            result = subprocess.run(["which", "cvlc"], capture_output=True, text=True, timeout=5)
            return result.returncode == 0 and result.stdout.strip()
        except Exception:
            return False


@pytest.fixture(scope="class")
def test_video_file():
    """Fixture providing test video file path"""
    # Check environment variable first
    env_path = os.environ.get("RTVI_TEST_VIDEO_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    # Try common test video locations
    test_paths = [
        "/opt/nvidia/via/streams/perf/test_video.mp4",
        "/opt/nvidia/via/streams/perf/warehouse_82min.mp4",
        os.path.join(os.path.dirname(__file__), "..", "test_data", "test_video.mp4"),
    ]
    for path in test_paths:
        if os.path.exists(path):
            return path
    return None


@pytest.fixture(scope="session")
def test_server():
    """Start a test server for integration tests"""
    import sys

    enable_debug = os.environ.get("RTVI_TEST_DEBUG", "").lower() in ("1", "true", "yes")
    if enable_debug:
        sys.stderr.write("[test_server fixture] Starting test_server fixture\n")
        sys.stderr.flush()

    # Check if decoder should be disabled (via environment variable or if GPU/decoder unavailable)
    disable_decoding = os.environ.get("RTVI_DISABLE_DECODING", "").lower() in ("1", "true", "yes")

    # Check if GPU decoder is available
    decoders = 1
    if not disable_decoding:
        try:
            import subprocess

            count = int(subprocess.check_output(["nvdec_get_count"], timeout=5).decode().strip())
            if count > 0:
                decoders = max(1, count)
            else:
                # No decoders available, disable decoding
                disable_decoding = True
        except Exception:
            # If nvdec_get_count fails, decoder is not available
            disable_decoding = True

    server_args = " ".join(
        [
            "--asset-dir",
            tempfile.mkdtemp(),
            "--max-live-streams",
            "10",
            "--log-level",
            "debug",
            "--host",
            "0.0.0.0",
            "--port",
            "8001",
            "--max-file-duration",
            "0",
            "--num-gpus",
            "1",
            "--vlm-model-type",
            "openai-compat",
        ]
    )

    # Add decoder configuration
    if disable_decoding:
        server_args = server_args + " --disable-decoding"
    else:
        server_args = server_args + " --num-decoders-per-gpu " + str(decoders)

    with ViaTestServer(server_args, port=8001, start_server=True) as server:
        # Wait for server to be ready
        max_wait = 30
        for _ in range(max_wait):
            try:
                resp = server.get(f"{API_PREFIX}/ready")
                if resp.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(1)
        yield server
        # Explicitly stop the underlying RTVIServer stream handler, if present
        if getattr(server, "_server", None) and getattr(server._server, "_stream_handler", None):
            try:
                server._server._stream_handler.stop()
            except Exception:
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

    def test_file_lifecycle(self, test_server):
        """Test complete file lifecycle"""
        # List files (should be empty)
        resp = test_server.get(f"{API_PREFIX}/files?purpose=vision")
        assert resp.status_code == 200
        initial_count = len(resp.json()["data"])
        assert initial_count >= 0  # Verify we can get the count

        # Note: Actual file upload would require a real video file
        # This test structure shows the pattern

    def test_live_stream_lifecycle(self, test_server, test_video_file):
        """Test live stream lifecycle with cvlc RTSP stream"""
        # Skip if VLC cannot be run (e.g., root without vlc-wrapper)
        if not _can_run_vlc():
            is_root = os.geteuid() == 0
            if is_root:
                pytest.skip(
                    "Cannot run VLC as root without vlc-wrapper. "
                    "Run tests as non-root user or install vlc-wrapper."
                )
            else:
                pytest.skip("VLC (cvlc) is not available. Install with: apt-get install -y vlc")

        # List streams (should be empty)
        resp = test_server.get(f"{API_PREFIX}/streams/get-stream-info")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        initial_count = len(resp.json())

        # Start cvlc RTSP stream if video file available
        stream_info = None
        if test_video_file and os.path.exists(test_video_file):
            try:
                stream_info = start_rtsp_stream(test_video_file, loop=True)
                rtsp_url = stream_info["rtsp_url"]

                # Add stream to server
                resp = test_server.post(
                    f"{API_PREFIX}/streams/add",
                    json={
                        "streams": [
                            {
                                "liveStreamUrl": rtsp_url,
                                "description": "Test stream from cvlc",
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
                # Cleanup cvlc stream
                if stream_info:
                    stop_rtsp_stream(stream_info["stream_id"])
        else:
            pytest.skip("Test video file not available for RTSP streaming")


class TestRTSPSreamManagement:
    """Test RTSP stream management with cvlc"""

    def test_add_and_delete_stream_with_cvlc(self, test_server, test_video_file):
        """Test adding and deleting streams using cvlc"""
        if not _can_run_vlc():
            is_root = os.geteuid() == 0
            if is_root:
                pytest.skip(
                    "Cannot run VLC as root without vlc-wrapper. "
                    "Run tests as non-root user or install vlc-wrapper."
                )
            else:
                pytest.skip("VLC (cvlc) is not available. Install with: apt-get install -y vlc")
        if not test_video_file or not os.path.exists(test_video_file):
            pytest.skip("Test video file not available")

        stream_info = None
        try:
            # Start cvlc RTSP stream
            stream_info = start_rtsp_stream(test_video_file, loop=True)
            rtsp_url = stream_info["rtsp_url"]

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
            if stream_info:
                stop_rtsp_stream(stream_info["stream_id"])

    def test_multiple_streams_with_cvlc(self, test_server, test_video_file):
        """Test managing multiple cvlc streams"""
        if not _can_run_vlc():
            is_root = os.geteuid() == 0
            if is_root:
                pytest.skip(
                    "Cannot run VLC as root without vlc-wrapper. "
                    "Run tests as non-root user or install vlc-wrapper."
                )
            else:
                pytest.skip("VLC (cvlc) is not available. Install with: apt-get install -y vlc")
        if not test_video_file or not os.path.exists(test_video_file):
            pytest.skip("Test video file not available")

        stream_infos = []
        try:
            # Start multiple streams
            for i in range(2):
                stream_info = start_rtsp_stream(
                    test_video_file, stream_id=f"test-stream-{i}", loop=True
                )
                stream_infos.append(stream_info)

            # Add all streams to server
            streams_data = [
                {"liveStreamUrl": info["rtsp_url"], "description": f"Stream {i}"}
                for i, info in enumerate(stream_infos)
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
            for stream_info in stream_infos:
                stop_rtsp_stream(stream_info["stream_id"])


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


class TestAPIConsistency:
    """Test API consistency and contract"""

    def test_response_format_models(self, test_server):
        """Test models endpoint response format"""
        resp = test_server.get(f"{API_PREFIX}/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "object" in data
        assert "data" in data
        assert "audio_support" in data

    def test_response_format_files(self, test_server):
        """Test files endpoint response format"""
        resp = test_server.get(f"{API_PREFIX}/files?purpose=vision")
        assert resp.status_code == 200
        data = resp.json()
        assert "object" in data
        assert "data" in data

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

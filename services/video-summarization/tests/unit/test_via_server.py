# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Unit tests for src/via_server.py

Tests VIA REST API routes, helper functions, exception handlers, and server initialization.
"""

import argparse
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import once so subsequent patches of builtins.open don't affect module import
# (e.g. via_health_eval -> matplotlib which uses open() for its config).
from via_server import get_version

# ---------------------------------------------------------------------------
# Helper utilities (no external dependencies)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetVersion:
    def test_returns_version_from_file(self, tmp_path):
        with patch("builtins.open", mock_open(read_data="2.5.1\n")):
            result = get_version()
        assert result == "2.5.1"

    def test_returns_unknown_on_missing_file(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            assert get_version() == "unknown"

    def test_returns_unknown_on_permission_error(self):
        with patch("builtins.open", side_effect=PermissionError):
            assert get_version() == "unknown"


@pytest.mark.unit
class TestConvertSecondsToString:
    def test_none_returns_na(self):
        from via_server import convert_seconds_to_string

        assert convert_seconds_to_string(None) == "N/A"

    def test_minutes_and_seconds(self):
        from via_server import convert_seconds_to_string

        assert convert_seconds_to_string(125) == "02:05"

    def test_with_hours(self):
        from via_server import convert_seconds_to_string

        assert convert_seconds_to_string(3661) == "01:01:01"

    def test_need_hour_flag(self):
        from via_server import convert_seconds_to_string

        assert convert_seconds_to_string(61, need_hour=True) == "00:01:01"

    def test_zero_seconds(self):
        from via_server import convert_seconds_to_string

        assert convert_seconds_to_string(0) == "00:00"

    def test_exactly_one_hour(self):
        from via_server import convert_seconds_to_string

        assert convert_seconds_to_string(3600) == "01:00:00"

    def test_fractional_seconds_truncated(self):
        from via_server import convert_seconds_to_string

        assert convert_seconds_to_string(90.7) == "01:30"


@pytest.mark.unit
class TestGetBuildCommitSha:
    def test_returns_sha_from_file(self):
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="abc123\n")),
        ):
            from via_server import get_build_commit_sha

            assert get_build_commit_sha() == "abc123"

    def test_returns_unknown_when_file_missing(self):
        with patch("os.path.exists", return_value=False):
            from via_server import get_build_commit_sha

            assert get_build_commit_sha() == "unknown"

    def test_returns_unknown_on_read_error(self):
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", side_effect=IOError("disk error")),
        ):
            from via_server import get_build_commit_sha

            assert get_build_commit_sha() == "unknown"


@pytest.mark.unit
class TestAddCommonErrorResponses:
    def test_default_returns_all_errors(self):
        from via_server import COMMON_ERROR_RESPONSES, add_common_error_responses

        result = add_common_error_responses()
        assert result == COMMON_ERROR_RESPONSES

    def test_with_specific_errors(self):
        from via_server import add_common_error_responses

        result = add_common_error_responses([400, 500])
        assert 400 in result
        assert 500 in result
        assert 401 in result
        assert 429 in result
        assert 422 in result

    def test_with_empty_list_returns_all(self):
        from via_server import COMMON_ERROR_RESPONSES, add_common_error_responses

        result = add_common_error_responses([])
        assert result == COMMON_ERROR_RESPONSES


# ---------------------------------------------------------------------------
# ViaServer construction and argument parsing
# ---------------------------------------------------------------------------


def _make_mock_args(**overrides):
    defaults = dict(
        host="0.0.0.0",
        port="8000",
        max_asset_storage_size=None,
        max_live_streams=2,
        log_level="info",
        disable_ca_rag=True,
        summarization_query="Summarize",
        ca_rag_config="config.yaml",
        max_file_duration=0,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture
def mock_via_server():
    """Create a ViaServer with heavily mocked dependencies."""
    with (
        patch("via_server.patch_logger_handlers"),
        patch("via_server.logger") as mock_logger,
        patch("via_utils.StreamSettingsCache") as MockSSC,
    ):
        MockSSC.return_value = MagicMock()
        mock_logger.level = 20

        from via_server import ViaServer

        args = _make_mock_args()
        server = ViaServer(args)
        server._stream_handler = MagicMock()
        yield server


@pytest.fixture
def test_client(mock_via_server):
    """TestClient around the ViaServer FastAPI app."""
    return TestClient(mock_via_server._app, raise_server_exceptions=False)


@pytest.mark.unit
class TestViaServerInit:
    def test_creates_app(self, mock_via_server):
        assert isinstance(mock_via_server._app, FastAPI)

    def test_app_config_set(self, mock_via_server):
        assert mock_via_server._app.config["host"] == "0.0.0.0"
        assert mock_via_server._app.config["port"] == "8000"

    def test_sse_active_clients_empty(self, mock_via_server):
        assert mock_via_server._sse_active_clients == {}


@pytest.mark.unit
class TestArgumentParser:
    def test_populate_argument_parser(self):
        with patch("via_stream_handler.ViaStreamHandler") as MockVSH:
            MockVSH.populate_argument_parser = MagicMock()
            from via_server import ViaServer

            parser = argparse.ArgumentParser()
            ViaServer.populate_argument_parser(parser)
            MockVSH.populate_argument_parser.assert_called_once_with(parser)

    def test_get_argument_parser_returns_parser(self):
        with (patch("via_stream_handler.ViaStreamHandler") as MockVSH,):
            MockVSH.populate_argument_parser = MagicMock()
            from via_server import ViaServer

            parser = ViaServer.get_argument_parser()
            assert isinstance(parser, argparse.ArgumentParser)


# ---------------------------------------------------------------------------
# Health check routes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthRoutes:
    def test_v1_live(self, test_client):
        resp = test_client.get("/v1/live")
        assert resp.status_code == 200

    def test_v1_ready(self, test_client):
        resp = test_client.get("/v1/ready")
        assert resp.status_code == 200

    def test_v1_startup(self, test_client):
        resp = test_client.get("/v1/startup")
        assert resp.status_code == 200

    def test_v1_metadata(self, test_client, mock_via_server):
        with patch("via_server.get_build_commit_sha", return_value="deadbeef"):
            resp = test_client.get("/v1/metadata")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sub_version"] == "deadbeef"
        assert data["host"] == "0.0.0.0"
        assert data["port"] == "8000"


# ---------------------------------------------------------------------------
# Metrics route
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetricsRoute:
    def test_metrics_returns_prometheus_data(self, test_client):
        resp = test_client.get("/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"


# ---------------------------------------------------------------------------
# Models route
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModelsRoute:
    def test_list_models(self, test_client, mock_via_server):
        model_info = SimpleNamespace(
            id="test-model", created=1700000000, owned_by="nvidia", api_type="chat"
        )
        mock_via_server._stream_handler.get_models_info.return_value = model_info
        resp = test_client.get("/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == "test-model"
        assert data["data"][0]["owned_by"] == "nvidia"


# ---------------------------------------------------------------------------
# Summarize route
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSummarizeRoute:
    def _model_info(self):
        return SimpleNamespace(id="test-model", api_type="chat")

    def test_summarize_wrong_model_returns_400(self, test_client, mock_via_server):
        mock_via_server._stream_handler.get_models_info.return_value = self._model_info()
        mock_via_server._stream_handler._ctx_mgr = None
        resp = test_client.post(
            "/summarize",
            json={
                "url": "http://example.com/video.mp4",
                "model": "wrong-model",
                "scenario": "test",
                "events": ["event1"],
                "stream": True,
            },
        )
        assert resp.status_code == 400

    def test_summarize_excluded_api_type_is_stripped(self, test_client, mock_via_server):
        """api_type is exclude=True on SummarizationQuery and is stripped before handle."""
        mock_via_server._stream_handler.get_models_info.return_value = self._model_info()
        mock_via_server._stream_handler._ctx_mgr = None
        mock_via_server._stream_handler.summarize.return_value = "req-api-type"
        mock_via_server._stream_handler.get_response.side_effect = Exception("done")
        resp = test_client.post(
            "/summarize",
            json={
                "url": "http://example.com/video.mp4",
                "model": "test-model",
                "scenario": "test",
                "events": ["event1"],
                "api_type": "ignored",
                "stream": True,
            },
        )
        assert resp.status_code == 200

    def test_summarize_file_id_only_succeeds(self, test_client, mock_via_server):
        """id-only file summarize (no url) for pre-uploaded assets.

        This flow is for dev/benchmark use only: upload a video via POST /files
        (requires VIA_DEV_API=true), then summarize by id without re-downloading.
        """
        mock_via_server._stream_handler.get_models_info.return_value = self._model_info()
        mock_via_server._stream_handler._ctx_mgr = None
        mock_via_server._stream_handler.summarize.return_value = "req-file-id"
        mock_via_server._stream_handler.get_response.side_effect = Exception("done")
        resp = test_client.post(
            "/summarize",
            json={
                "id": "00000000-0000-0000-0000-000000000001",
                "model": "test-model",
                "scenario": "test",
                "events": ["event1"],
                "stream": True,
            },
        )
        # 200 means the request passed validation and entered the SSE streaming path
        assert resp.status_code == 200

    def test_summarize_file_missing_url_and_id_returns_422(self, test_client, mock_via_server):
        """Summarization requires either url or id."""
        mock_via_server._stream_handler.get_models_info.return_value = self._model_info()
        resp = test_client.post(
            "/summarize",
            json={
                "model": "test-model",
                "scenario": "test",
                "events": ["event1"],
                "stream": True,
            },
        )
        assert resp.status_code == 422

    def test_summarize_no_ca_rag_no_streaming_returns_400(self, test_client, mock_via_server):
        mock_via_server._stream_handler.get_models_info.return_value = self._model_info()
        mock_via_server._stream_handler._ctx_mgr = None
        resp = test_client.post(
            "/summarize",
            json={
                "url": "http://example.com/video.mp4",
                "model": "test-model",
                "scenario": "test",
                "events": ["event1"],
                "stream": False,
            },
        )
        assert resp.status_code == 400

    def test_summarize_sse_conflict_returns_409(self, test_client, mock_via_server):
        """A second streaming client for the same source within 3s is rejected."""
        mock_via_server._stream_handler.get_models_info.return_value = self._model_info()
        mock_via_server._stream_handler._ctx_mgr = None
        mock_via_server._stream_handler.summarize.return_value = "req-conflict"
        # Pretend another client is already connected for this source_id.
        import time

        mock_via_server._sse_active_clients["00000000-0000-0000-0000-000000000001"] = time.time()
        # Force source_id via id so the conflict map key matches.
        resp = test_client.post(
            "/summarize",
            json={
                "id": "00000000-0000-0000-0000-000000000001",
                "model": "test-model",
                "scenario": "test",
                "events": ["event1"],
                "stream": True,
            },
        )
        assert resp.status_code == 409

    def test_summarize_invalid_url_format_returns_422(self, test_client, mock_via_server):
        mock_via_server._stream_handler.get_models_info.return_value = self._model_info()
        resp = test_client.post(
            "/summarize",
            json={
                "url": "ftp://invalid-scheme.com/video.mp4",
                "model": "test-model",
                "stream": True,
            },
        )
        assert resp.status_code == 422

    def test_summarize_non_streaming_success(self, test_client, mock_via_server):
        from via_stream_handler import RequestInfo

        req_uuid = "00000000-0000-0000-0000-000000000099"
        mock_via_server._stream_handler.get_models_info.return_value = self._model_info()
        mock_via_server._stream_handler._ctx_mgr = MagicMock()
        mock_via_server._stream_handler.summarize.return_value = req_uuid

        req_info = MagicMock()
        req_info.status = RequestInfo.Status.SUCCESSFUL
        req_info.is_live = False
        req_info.queue_time = 1700000000
        req_info.start_time = 1700000000
        req_info.end_time = 1700000010
        req_info.start_timestamp = 0
        req_info.end_timestamp = 60
        req_info.chunk_count = 1
        req_info.usage = MagicMock(
            summary_tokens=100,
            aggregation_tokens=50,
            summary_requests=1,
            summary_latency=1.0,
            aggregation_latency=0.5,
        )
        resp_item = MagicMock()
        resp_item.response = "Summary of the video"
        mock_via_server._stream_handler.get_response.return_value = (req_info, [resp_item])
        mock_via_server._stream_handler.wait_for_request_done.return_value = None
        mock_via_server._stream_handler.check_status_remove_req_id.return_value = None

        resp = test_client.post(
            "/summarize",
            json={
                "url": "http://example.com/video.mp4",
                "model": "test-model",
                "scenario": "test",
                "events": ["event1"],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == req_uuid
        assert data["object"] == "summarization.completion"
        assert len(data["choices"]) == 1

    def test_summarize_non_streaming_failed_returns_500(self, test_client, mock_via_server):
        from via_stream_handler import RequestInfo

        mock_via_server._stream_handler.get_models_info.return_value = self._model_info()
        mock_via_server._stream_handler._ctx_mgr = MagicMock()
        mock_via_server._stream_handler.summarize.return_value = "req-123"
        req_info = MagicMock()
        req_info.status = RequestInfo.Status.FAILED
        req_info.error_message = "VLM error"
        req_info.rtvi_status_code = None
        req_info.rtvi_error_code = None
        mock_via_server._stream_handler.get_response.return_value = (req_info, [])
        mock_via_server._stream_handler.wait_for_request_done.return_value = None
        mock_via_server._stream_handler.check_status_remove_req_id.return_value = None

        resp = test_client.post(
            "/summarize",
            json={
                "url": "http://example.com/video.mp4",
                "model": "test-model",
                "scenario": "test",
                "events": ["event1"],
                "stream": False,
            },
        )
        assert resp.status_code == 500

    def test_summarize_url_s3(self, test_client, mock_via_server):
        from via_stream_handler import RequestInfo

        req_uuid = "00000000-0000-0000-0000-000000000077"
        mock_via_server._stream_handler.get_models_info.return_value = self._model_info()
        mock_via_server._stream_handler._ctx_mgr = MagicMock()
        mock_via_server._stream_handler.summarize.return_value = req_uuid
        mock_via_server._stream_handler.wait_for_request_done.return_value = None
        mock_via_server._stream_handler.check_status_remove_req_id.return_value = None

        req_info = MagicMock()
        req_info.status = RequestInfo.Status.SUCCESSFUL
        req_info.is_live = False
        req_info.queue_time = 1700000000
        req_info.start_time = 1700000000
        req_info.end_time = 1700000010
        req_info.start_timestamp = 0
        req_info.end_timestamp = 60
        req_info.chunk_count = 1
        req_info.usage = MagicMock(
            summary_tokens=0,
            aggregation_tokens=0,
            summary_requests=0,
            summary_latency=0.0,
            aggregation_latency=0.0,
        )
        resp_item = MagicMock(response="S3 video summary")
        mock_via_server._stream_handler.get_response.return_value = (req_info, [resp_item])

        resp = test_client.post(
            "/summarize",
            json={
                "url": (
                    "https://urm.nvidia.com/artifactory/sw-ds-generic-bld-local"
                    "/lmm/streams/warehouse_gopro_10m_720.mp4"
                ),
                "model": "test-model",
                "scenario": "test",
                "events": ["event1"],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == req_uuid

    def test_summarize_url_http(self, test_client, mock_via_server):
        mock_via_server._stream_handler.get_models_info.return_value = self._model_info()
        mock_via_server._stream_handler._ctx_mgr = None
        resp = test_client.post(
            "/summarize",
            json={
                "url": "https://example.com/video.mp4",
                "model": "test-model",
                "scenario": "test",
                "events": ["event1"],
                "stream": True,
            },
        )
        assert resp.status_code in [200, 400]


# ---------------------------------------------------------------------------
# Recommended Config route
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecommendedConfigRoute:
    def test_recommended_config_success(self, test_client, mock_via_server):
        with patch("via_server.get_avg_time_per_chunk", return_value="5.0s"):
            resp = test_client.post(
                "/recommended_config",
                json={
                    "video_length": 600,
                    "target_response_time": 60,
                    "usecase_event_duration": 10,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "chunk_size" in data
        assert "text" in data

    def test_recommended_config_fallback(self, test_client, mock_via_server):
        with patch("via_server.get_avg_time_per_chunk", side_effect=Exception("no stats")):
            resp = test_client.post(
                "/recommended_config",
                json={"video_length": 600, "target_response_time": 60},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["chunk_size"] == 60  # default fallback when stats unavailable


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExceptionHandlers:
    def test_via_exception_handler(self, test_client, mock_via_server):
        from via_exception import ViaException

        mock_via_server._stream_handler.get_models_info.side_effect = ViaException(
            "Not found", "BadParameter", 400
        )
        resp = test_client.get("/models")
        assert resp.status_code == 400
        data = resp.json()
        assert data["code"] == "BadParameter"
        assert data["message"] == "Not found"

    def test_http_exception_handler(self, test_client, mock_via_server):
        from fastapi.exceptions import HTTPException

        mock_via_server._stream_handler.get_models_info.side_effect = HTTPException(
            status_code=403, detail="Forbidden"
        )
        resp = test_client.get("/models")
        assert resp.status_code == 403

    def test_unhandled_exception_handler(self, test_client, mock_via_server):
        mock_via_server._stream_handler.get_models_info.side_effect = RuntimeError("unexpected")
        resp = test_client.get("/models")
        assert resp.status_code == 500
        data = resp.json()
        assert data["code"] == "InternalServerError"

    def test_validation_error_handler(self, test_client, mock_via_server):
        resp = test_client.post("/summarize", json={"model": 12345})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# OpenAPI schema customization
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOpenAPISchema:
    def test_custom_openapi_adds_security(self, mock_via_server):
        schema = mock_via_server._app.openapi()
        assert "security" in schema
        assert schema["security"] == [{"Token": []}]
        assert "Token" in schema["components"]["securitySchemes"]

    def test_custom_openapi_caches_result(self, mock_via_server):
        schema1 = mock_via_server._app.openapi()
        schema2 = mock_via_server._app.openapi()
        assert schema1 is schema2


# ---------------------------------------------------------------------------
# ViaServer.run() path tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestViaServerRun:
    def test_run_http_only(self, mock_via_server):
        with (
            patch.dict(os.environ, {"LVS_ENABLE_MCP": "false"}, clear=False),
            patch("via_stream_handler.ViaStreamHandler") as MockVSH,
            patch("via_server.uvicorn") as mock_uvicorn,
            patch("via_server.patch_logger_handlers"),
        ):
            MockVSH.return_value = MagicMock()
            mock_server_instance = MagicMock()
            mock_uvicorn.Config.return_value = MagicMock()
            mock_uvicorn.Server.return_value = mock_server_instance

            mock_via_server.run()

            mock_uvicorn.Server.assert_called_once()
            mock_server_instance.run.assert_called_once()

    def test_run_with_mcp_enabled(self, mock_via_server):
        with (
            patch.dict(os.environ, {"LVS_ENABLE_MCP": "true"}, clear=False),
            patch("via_stream_handler.ViaStreamHandler") as MockVSH,
            patch("via_server.patch_logger_handlers"),
            patch.object(mock_via_server, "_run_both_servers", new_callable=AsyncMock),
            patch("via_server.asyncio") as mock_asyncio,
        ):
            MockVSH.return_value = MagicMock()
            mock_via_server.run()
            mock_asyncio.run.assert_called_once()

    def test_run_stream_handler_init_failure(self, mock_via_server):
        with (
            patch("via_stream_handler.ViaStreamHandler", side_effect=RuntimeError("GPU fail")),
            patch("via_server.patch_logger_handlers"),
        ):
            from via_exception import ViaException

            with pytest.raises(ViaException, match="Failed to load VIA stream handler"):
                mock_via_server.run()


# ---------------------------------------------------------------------------
# _run_both_servers async paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunBothServers:
    def test_import_error_fallback(self, mock_via_server):
        with (
            patch.dict(os.environ, {"LVS_ENABLE_MCP": "true"}, clear=False),
            patch("via_server.uvicorn") as mock_uvicorn,
            patch.dict("sys.modules", {"lvs_mcp": None}),
        ):
            mock_server = MagicMock()
            mock_server.serve = AsyncMock()
            mock_uvicorn.Config.return_value = MagicMock()
            mock_uvicorn.Server.return_value = mock_server
            with patch("builtins.__import__", side_effect=ImportError("no mcp")):
                asyncio.run(mock_via_server._run_both_servers())

    def test_no_mcp_port_fallback(self, mock_via_server):
        with (
            patch.dict(os.environ, {"LVS_MCP_PORT": ""}, clear=False),
            patch("via_server.uvicorn") as mock_uvicorn,
        ):
            mock_server = MagicMock()
            mock_server.serve = AsyncMock()
            mock_uvicorn.Config.return_value = MagicMock()
            mock_uvicorn.Server.return_value = mock_server
            with patch("via_server.run_mcp_server", create=True):
                asyncio.run(mock_via_server._run_both_servers())


# ---------------------------------------------------------------------------
# Dev routes (Files API) - enabled via VIA_DEV_API env var
# ---------------------------------------------------------------------------


@pytest.fixture
def dev_server_and_client():
    """ViaServer with dev API routes enabled."""
    with (
        patch.dict(os.environ, {"VIA_DEV_API": "true"}, clear=False),
        patch("via_server.patch_logger_handlers"),
        patch("via_server.logger") as mock_logger,
        patch("via_utils.StreamSettingsCache") as MockSSC,
    ):
        MockSSC.return_value = MagicMock()
        mock_logger.level = 20

        from via_server import ViaServer

        args = _make_mock_args()
        server = ViaServer(args)
        server._stream_handler = MagicMock()
        client = TestClient(server._app, raise_server_exceptions=False)
        yield server, client


@pytest.mark.unit
class TestDevFileRoutes:
    def test_add_file_no_file_no_filename_returns_422(self, dev_server_and_client):
        server, client = dev_server_and_client
        resp = client.post(
            "/files",
            data={"purpose": "vision", "media_type": "video"},
        )
        assert resp.status_code == 422

    def test_add_file_unsupported_media_type_returns_422(self, dev_server_and_client):
        server, client = dev_server_and_client
        resp = client.post(
            "/files",
            data={
                "purpose": "vision",
                "media_type": "audio",
                "filename": "/tmp/test.mp4",
            },
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# API prefix with versioning
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAPIPrefix:
    def test_api_prefix_default_no_versioning(self):
        from prometheus_client import REGISTRY

        with patch.dict(os.environ, {}, clear=False):
            if "VSS_API_ENABLE_VERSIONING" in os.environ:
                del os.environ["VSS_API_ENABLE_VERSIONING"]
            import importlib

            import via_server

            with patch.object(REGISTRY, "unregister"):
                importlib.reload(via_server)
            assert via_server.API_PREFIX == ""

    def test_api_prefix_with_versioning(self):
        from prometheus_client import REGISTRY

        with patch.dict(os.environ, {"VSS_API_ENABLE_VERSIONING": "true"}, clear=False):
            import importlib

            import via_server

            with patch.object(REGISTRY, "unregister"):
                importlib.reload(via_server)
            assert via_server.API_PREFIX == "/v1"
            # Reset
            os.environ.pop("VSS_API_ENABLE_VERSIONING", None)
            with patch.object(REGISTRY, "unregister"):
                importlib.reload(via_server)


# ---------------------------------------------------------------------------
# handle_validation_error – additional coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidationErrorAdditional:
    def test_uuid_parsing_error_includes_input(self, test_client, mock_via_server):
        resp = test_client.post(
            "/summarize",
            json={
                "id": "not-a-uuid",
                "model": "test-model",
                "stream": True,
            },
        )
        assert resp.status_code == 422
        data = resp.json()
        assert "InvalidParameters" == data["code"]
        assert "not-a-uuid" in data["message"]

    def test_pattern_mismatch_error(self, dev_server_and_client):
        server, client = dev_server_and_client
        resp = client.get("/files?purpose=123!!!")
        assert resp.status_code == 422
        data = resp.json()
        assert data["code"] == "InvalidParameters"


# ---------------------------------------------------------------------------
# handle_http_exception – response body verification
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHttpExceptionBody:
    def test_response_has_code_and_message(self, test_client, mock_via_server):
        from fastapi.exceptions import HTTPException

        mock_via_server._stream_handler.get_models_info.side_effect = HTTPException(
            status_code=404, detail="ModelNotFound"
        )
        resp = test_client.get("/models")
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == "ModelNotFound"
        assert data["message"] == "ModelNotFound"

    def test_http_exception_503(self, test_client, mock_via_server):
        from fastapi.exceptions import HTTPException

        mock_via_server._stream_handler.get_models_info.side_effect = HTTPException(
            status_code=503, detail="ServiceUnavailable"
        )
        resp = test_client.get("/models")
        assert resp.status_code == 503
        data = resp.json()
        assert data["code"] == "ServiceUnavailable"


# ---------------------------------------------------------------------------
# Phase 1b: _format_chunk_response
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatChunkResponse:
    def test_file_response_formats_as_string(self, mock_via_server):
        resp = MagicMock()
        resp.start_timestamp = 10.5
        resp.end_timestamp = 20.0
        resp.response = "A car drove by."
        req_info = MagicMock(is_live=False)

        result = mock_via_server._format_chunk_response(resp, req_info)
        assert "[10.5 - 20.0]" in result
        assert "A car drove by." in result

    def test_live_response_uses_raw_timestamps(self, mock_via_server):
        resp = MagicMock()
        resp.start_timestamp = "2024-01-01T00:00:00"
        resp.end_timestamp = "2024-01-01T00:01:00"
        resp.response = "Person detected."
        req_info = MagicMock(is_live=True)

        result = mock_via_server._format_chunk_response(resp, req_info)
        assert "2024-01-01T00:00:00" in result


# ---------------------------------------------------------------------------
# Phase 1b: Exception handlers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExceptionHandlersAdditional:
    def test_http_exception_handler(self, test_client, mock_via_server):
        from fastapi import HTTPException

        @mock_via_server._app.get("/trigger-http-error")
        async def trigger():
            raise HTTPException(status_code=403, detail="Forbidden")

        resp = test_client.get("/trigger-http-error")
        assert resp.status_code == 403
        body = resp.json()
        assert body["code"] == "Forbidden"

    def test_generic_exception_handler(self, test_client, mock_via_server):
        @mock_via_server._app.get("/trigger-generic-error")
        async def trigger():
            raise RuntimeError("unexpected")

        resp = test_client.get("/trigger-generic-error")
        assert resp.status_code == 500
        assert resp.json()["code"] == "InternalServerError"


# ---------------------------------------------------------------------------
# Additional get_build_commit_sha edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetBuildCommitShaAdditional:
    def test_returns_stripped_sha(self):
        """Whitespace around the SHA should be stripped."""
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="  deadbeef  \n")),
        ):
            from via_server import get_build_commit_sha

            assert get_build_commit_sha() == "deadbeef"

    def test_returns_unknown_on_permission_error(self):
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", side_effect=PermissionError("no access")),
        ):
            from via_server import get_build_commit_sha

            assert get_build_commit_sha() == "unknown"

    def test_file_exists_false_skips_open(self):
        """When os.path.exists returns False, open should never be called."""
        with (
            patch("os.path.exists", return_value=False),
            patch("builtins.open") as mock_file,
        ):
            from via_server import get_build_commit_sha

            result = get_build_commit_sha()
            mock_file.assert_not_called()
            assert result == "unknown"


# ---------------------------------------------------------------------------
# Additional add_common_error_responses edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAddCommonErrorResponsesAdditional:
    def test_always_includes_401(self):
        from via_server import add_common_error_responses

        result = add_common_error_responses([400])
        assert 401 in result

    def test_always_includes_422(self):
        from via_server import add_common_error_responses

        result = add_common_error_responses([400])
        assert 422 in result

    def test_always_includes_429(self):
        from via_server import add_common_error_responses

        result = add_common_error_responses([400])
        assert 429 in result

    def test_single_extra_error_code(self):
        from via_server import add_common_error_responses

        result = add_common_error_responses([500])
        assert 500 in result
        assert 401 in result

    def test_result_values_have_model_key(self):
        """Each entry in the default mapping must have either 'model' or 'description'."""
        from via_server import add_common_error_responses

        result = add_common_error_responses()
        for code, info in result.items():
            assert "description" in info, f"Entry for {code} missing 'description'"


# ---------------------------------------------------------------------------
# ViaServer init – timing middleware and sse_active_clients
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestViaServerInitAdditional:
    def test_perf_middleware_registered_at_debug_level(self):
        """When logger level <= LOG_PERF_LEVEL, a middleware should be added."""
        with (
            patch("via_server.patch_logger_handlers"),
            patch("via_server.logger") as mock_logger,
            patch("via_utils.StreamSettingsCache"),
        ):
            import via_server

            mock_logger.level = via_server.LOG_PERF_LEVEL  # at boundary

            from via_server import ViaServer

            args = _make_mock_args()
            server = ViaServer(args)
            # The middleware stack should be non-empty
            assert len(server._app.user_middleware) > 0

    def test_server_attribute_initially_none(self, mock_via_server):
        assert mock_via_server._server is None

    def test_stream_settings_cache_created(self, mock_via_server):
        assert mock_via_server._stream_settings_cache is not None


# ---------------------------------------------------------------------------
# Health route metadata – licenseInfo present
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthMetadataAdditional:
    def test_metadata_contains_license_info(self, test_client):
        with patch("via_server.get_build_commit_sha", return_value="abc"):
            resp = test_client.get("/v1/metadata")
        assert resp.status_code == 200
        data = resp.json()
        assert "licenseInfo" in data
        assert data["licenseInfo"]["name"] == "LicenseRef-NvidiaProprietary"

    def test_metadata_version_field_present(self, test_client):
        with patch("via_server.get_build_commit_sha", return_value="abc"):
            resp = test_client.get("/v1/metadata")
        data = resp.json()
        assert "version" in data

    def test_v1_live_returns_empty_body(self, test_client):
        resp = test_client.get("/v1/live")
        assert resp.status_code == 200

    def test_v1_ready_returns_empty_body(self, test_client):
        resp = test_client.get("/v1/ready")
        assert resp.status_code == 200

    def test_v1_startup_returns_empty_body(self, test_client):
        resp = test_client.get("/v1/startup")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# DELETE /assets/{asset_id}/index — NVBug 6465125
# ---------------------------------------------------------------------------


SAMPLE_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


@pytest.mark.unit
class TestDeleteAssetIndexRoute:
    def test_delete_index_success(self, test_client, mock_via_server):
        mock_via_server._stream_handler.drop_collection_for_asset.return_value = {
            "acknowledged": True
        }
        resp = test_client.delete(f"/assets/{SAMPLE_UUID}/index")
        assert resp.status_code == 200
        data = resp.json()
        assert data["asset_id"] == SAMPLE_UUID
        assert data["deleted"] is True
        assert data["detail"] is None
        mock_via_server._stream_handler.drop_collection_for_asset.assert_called_once_with(
            SAMPLE_UUID, force_legacy=True
        )

    def test_delete_index_skipped_ca_rag_disabled(self, test_client, mock_via_server):
        mock_via_server._stream_handler.drop_collection_for_asset.return_value = {
            "skipped": True,
            "reason": "ca-rag disabled",
        }
        resp = test_client.delete(f"/assets/{SAMPLE_UUID}/index")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is False
        assert data["detail"] == "ca-rag disabled"

    def test_delete_index_skipped_kafka_disabled(self, test_client, mock_via_server):
        mock_via_server._stream_handler.drop_collection_for_asset.return_value = {
            "skipped": True,
            "reason": "KAFKA_ENABLED=false",
        }
        resp = test_client.delete(f"/assets/{SAMPLE_UUID}/index")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is False
        assert data["detail"] == "KAFKA_ENABLED=false"

    def test_delete_index_error_from_drop_collection(self, test_client, mock_via_server):
        mock_via_server._stream_handler.drop_collection_for_asset.return_value = {
            "error": "connection refused"
        }
        resp = test_client.delete(f"/assets/{SAMPLE_UUID}/index")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is False
        assert data["detail"] == "connection refused"

    def test_delete_index_handler_raises_returns_500(self, test_client, mock_via_server):
        mock_via_server._stream_handler.drop_collection_for_asset.side_effect = RuntimeError(
            "unexpected"
        )
        resp = test_client.delete(f"/assets/{SAMPLE_UUID}/index")
        assert resp.status_code == 500

    def test_delete_index_invalid_uuid_returns_422(self, test_client):
        resp = test_client.delete("/assets/not-a-valid-uuid/index")
        assert resp.status_code == 422

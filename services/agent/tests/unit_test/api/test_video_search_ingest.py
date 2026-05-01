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
"""Unit tests for video_search_ingest module."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from vss_agents.api.video_search_ingest import ALLOWED_VIDEO_TYPES
from vss_agents.api.video_search_ingest import VideoIngestResponse
from vss_agents.api.video_search_ingest import VideoUploadCompleteInput
from vss_agents.api.video_search_ingest import _parse_optional_http_url
from vss_agents.api.video_search_ingest import _run_post_upload_processing
from vss_agents.api.video_search_ingest import create_video_search_ingest_router
from vss_agents.api.video_search_ingest import register_video_search_ingest_routes


class TestAllowedVideoTypes:
    """Test ALLOWED_VIDEO_TYPES constant."""

    def test_mp4_allowed(self):
        assert "video/mp4" in ALLOWED_VIDEO_TYPES

    def test_mkv_allowed(self):
        assert "video/x-matroska" in ALLOWED_VIDEO_TYPES

    def test_only_two_types(self):
        assert len(ALLOWED_VIDEO_TYPES) == 2


class TestVideoIngestResponse:
    """Test VideoIngestResponse model."""

    def test_response_creation(self):
        response = VideoIngestResponse(
            message="Upload complete", video_id="video-001", filename="test_video.mp4", chunks_processed=10
        )
        assert response.message == "Upload complete"
        assert response.video_id == "video-001"
        assert response.filename == "test_video.mp4"
        assert response.chunks_processed == 10

    def test_response_default_chunks(self):
        response = VideoIngestResponse(message="Done", video_id="vid-002", filename="another_video.mp4")
        assert response.chunks_processed == 0

    def test_response_serialization(self):
        response = VideoIngestResponse(
            message="Test", video_id="test-id", filename="serialized_video.mp4", chunks_processed=5
        )
        data = response.model_dump()
        assert data["message"] == "Test"
        assert data["video_id"] == "test-id"
        assert data["filename"] == "serialized_video.mp4"
        assert data["chunks_processed"] == 5


class TestCreateVideoSearchIngestRouter:
    """Test create_video_search_ingest_router function."""

    def test_create_router(self):
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )
        assert router is not None

    def test_create_router_custom_params(self):
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080",
            rtvi_embed_base_url="http://rtvi:8080",
            rtvi_embed_model="custom-model",
            rtvi_embed_chunk_duration=10,
        )
        assert router is not None

    def test_router_has_routes(self):
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )
        # Router should have routes registered
        assert len(router.routes) > 0


class TestUploadVideoToVstEndpoint:
    """Test upload_video_to_vst endpoint logic."""

    @pytest.mark.asyncio
    async def test_successful_upload(self):
        """Test successful video upload flow."""
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )

        # Create mock request
        mock_request = MagicMock()
        mock_request.headers = {"content-type": "video/mp4", "content-length": "1024"}
        mock_request.stream = AsyncMock(return_value=iter([b"test data"]))

        # Mock external boundaries (HTTP + timeline helper)
        with (
            patch("vss_agents.api.video_search_ingest.httpx.AsyncClient") as mock_client_class,
            patch("vss_agents.api.video_search_ingest.get_timeline", new_callable=AsyncMock) as mock_get_timeline,
        ):
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_get_timeline.return_value = ("1000", "2000")

            # Mock VST upload response
            mock_vst_response = Mock()
            mock_vst_response.status_code = 200
            mock_vst_response.json = Mock(return_value={"sensorId": "sensor-123"})

            # Mock storage response
            mock_storage_response = Mock()
            mock_storage_response.status_code = 200
            mock_storage_response.json = Mock(return_value={"videoUrl": "http://vst/video.mp4"})

            # Mock embedding response
            mock_embed_response = Mock()
            mock_embed_response.status_code = 200
            mock_embed_response.json = Mock(return_value={"usage": {"total_chunks_processed": 5}})

            # Set up mock client responses
            mock_client.put.return_value = mock_vst_response
            mock_client.get.return_value = mock_storage_response
            mock_client.post.return_value = mock_embed_response

            # Get the endpoint function
            endpoint = router.routes[0].endpoint

            # Call the endpoint
            response = await endpoint(filename="test.mp4", request=mock_request)

            assert response.video_id == "sensor-123"
            assert response.chunks_processed == 5
            assert "successfully uploaded" in response.message

    @pytest.mark.asyncio
    async def test_missing_content_type(self):
        """Test error when Content-Type header is missing."""
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )

        mock_request = MagicMock()
        mock_request.headers = {"content-length": "1024"}

        endpoint = router.routes[0].endpoint

        with pytest.raises(HTTPException) as exc_info:
            await endpoint(filename="test.mp4", request=mock_request)

        assert exc_info.value.status_code == 400
        assert "Content-Type header is required" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_invalid_content_type(self):
        """Test error when Content-Type is not allowed."""
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )

        mock_request = MagicMock()
        mock_request.headers = {
            "content-type": "video/webm",  # Not allowed
            "content-length": "1024",
        }

        endpoint = router.routes[0].endpoint

        with pytest.raises(HTTPException) as exc_info:
            await endpoint(filename="test.mp4", request=mock_request)

        assert exc_info.value.status_code == 415
        assert "Unsupported video format" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_missing_content_length(self):
        """Test error when Content-Length header is missing."""
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )

        mock_request = MagicMock()
        mock_request.headers = {"content-type": "video/mp4"}

        endpoint = router.routes[0].endpoint

        with pytest.raises(HTTPException) as exc_info:
            await endpoint(filename="test.mp4", request=mock_request)

        assert exc_info.value.status_code == 400
        assert "Content-Length header is required" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_zero_content_length(self):
        """Test error when Content-Length is zero."""
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )

        mock_request = MagicMock()
        mock_request.headers = {"content-type": "video/mp4", "content-length": "0"}

        endpoint = router.routes[0].endpoint

        with pytest.raises(HTTPException) as exc_info:
            await endpoint(filename="test.mp4", request=mock_request)

        assert exc_info.value.status_code == 400
        assert "File is empty" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_invalid_content_length_format(self):
        """Test error when Content-Length is not a valid integer."""
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )

        mock_request = MagicMock()
        mock_request.headers = {"content-type": "video/mp4", "content-length": "invalid"}

        endpoint = router.routes[0].endpoint

        with pytest.raises(HTTPException) as exc_info:
            await endpoint(filename="test.mp4", request=mock_request)

        assert exc_info.value.status_code == 400
        assert "Invalid Content-Length" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_vst_upload_failure(self):
        """Test error when VST upload fails."""
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )

        mock_request = MagicMock()
        mock_request.headers = {"content-type": "video/mp4", "content-length": "1024"}
        mock_request.stream = AsyncMock(return_value=iter([b"test data"]))

        with (
            patch("vss_agents.api.video_search_ingest.httpx.AsyncClient") as mock_client_class,
            patch("vss_agents.api.video_search_ingest.get_timeline", new_callable=AsyncMock) as mock_get_timeline,
        ):
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_get_timeline.return_value = ("1000", "2000")

            mock_vst_response = Mock()
            mock_vst_response.status_code = 500
            mock_vst_response.text = "Server error"
            mock_client.put.return_value = mock_vst_response

            endpoint = router.routes[0].endpoint

            with pytest.raises(HTTPException) as exc_info:
                await endpoint(filename="test.mp4", request=mock_request)

            assert exc_info.value.status_code == 502
            assert "VST upload failed" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_filename_without_extension(self):
        """Test handling filename without extension."""
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )

        mock_request = MagicMock()
        mock_request.headers = {"content-type": "video/mp4", "content-length": "1024"}
        mock_request.stream = AsyncMock(return_value=iter([b"test data"]))

        with (
            patch("vss_agents.api.video_search_ingest.httpx.AsyncClient") as mock_client_class,
            patch("vss_agents.api.video_search_ingest.get_timeline", new_callable=AsyncMock) as mock_get_timeline,
        ):
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_get_timeline.return_value = ("1000", "2000")

            mock_vst_response = Mock()
            mock_vst_response.status_code = 200
            mock_vst_response.json = Mock(return_value={"sensorId": "sensor-123"})

            mock_storage_response = Mock()
            mock_storage_response.status_code = 200
            mock_storage_response.json = Mock(return_value={"videoUrl": "http://vst/video.mp4"})

            mock_embed_response = Mock()
            mock_embed_response.status_code = 200
            mock_embed_response.json = Mock(return_value={"usage": {"total_chunks_processed": 3}})

            mock_client.put.return_value = mock_vst_response
            mock_client.get.return_value = mock_storage_response
            mock_client.post.return_value = mock_embed_response

            endpoint = router.routes[0].endpoint
            response = await endpoint(filename="test_video", request=mock_request)

            assert response.video_id == "sensor-123"


class TestRegisterVideoSearchIngestRoutes:
    """Test register_video_search_ingest_routes function (videos-for-search routes)."""

    def test_register_with_config(self):
        """Test registering routes using config object."""
        mock_app = MagicMock()
        mock_config = MagicMock()

        mock_streaming_config = MagicMock()
        mock_streaming_config.vst_internal_url = "http://vst:8080"
        mock_streaming_config.rtvi_embed_base_url = "http://rtvi:8080"
        mock_streaming_config.rtvi_cv_base_url = ""
        mock_streaming_config.rtvi_embed_model = "test-model"
        mock_streaming_config.rtvi_embed_chunk_duration = 10

        mock_config.general.front_end.streaming_ingest = mock_streaming_config

        register_video_search_ingest_routes(mock_app, mock_config)

        assert mock_app.include_router.called

    def test_register_missing_streaming_ingest_raises(self):
        """Without streaming_ingest, registration must fail loudly."""
        mock_app = MagicMock()
        mock_config = MagicMock()
        mock_config.general.front_end = MagicMock(spec=[])

        with pytest.raises(ValueError, match="streaming_ingest"):
            register_video_search_ingest_routes(mock_app, mock_config)

    def test_register_missing_vst_url_raises(self):
        """streaming_ingest present but vst_internal_url empty must raise."""
        mock_app = MagicMock()
        mock_config = MagicMock()

        mock_streaming_config = MagicMock()
        mock_streaming_config.vst_internal_url = ""
        mock_streaming_config.rtvi_embed_base_url = "http://rtvi:8080"
        mock_streaming_config.rtvi_cv_base_url = ""
        mock_streaming_config.rtvi_embed_model = "cosmos-embed1-448p"
        mock_streaming_config.rtvi_embed_chunk_duration = 5

        mock_config.general.front_end.streaming_ingest = mock_streaming_config

        with pytest.raises(ValueError, match="vst_internal_url"):
            register_video_search_ingest_routes(mock_app, mock_config)

    def test_register_missing_rtvi_embed_url_raises(self):
        """videos-for-search is search-only — rtvi_embed_base_url is required."""
        mock_app = MagicMock()
        mock_config = MagicMock()

        mock_streaming_config = MagicMock()
        mock_streaming_config.vst_internal_url = "http://vst:8080"
        mock_streaming_config.rtvi_embed_base_url = ""
        mock_streaming_config.rtvi_cv_base_url = ""
        mock_streaming_config.rtvi_embed_model = "cosmos-embed1-448p"
        mock_streaming_config.rtvi_embed_chunk_duration = 5

        mock_config.general.front_end.streaming_ingest = mock_streaming_config

        with pytest.raises(ValueError, match="rtvi_embed_base_url"):
            register_video_search_ingest_routes(mock_app, mock_config)


class TestParseOptionalHttpUrl:
    """Tests for the shared URL-guard helper."""

    def test_none_and_empty(self):
        assert _parse_optional_http_url(None) is None
        assert _parse_optional_http_url("") is None

    def test_scheme_only_forms_rejected(self):
        # No hostname to connect to — can't be used as a service URL.
        assert _parse_optional_http_url("http://") is None
        assert _parse_optional_http_url("http:") is None

    def test_empty_port_body_rejected(self):
        # `http://host:` (trailing colon, empty port body) parses with
        # hostname="host" and port=None — Python urlparse silently leaves the
        # netloc as `host:`, so callers would fall back to the scheme's default
        # port (80) and connect to nothing. Treat it as misconfigured and let
        # callers skip the downstream step rather than hang on a TCP timeout.
        # See PR #179.
        assert _parse_optional_http_url("http://host:") is None

    def test_explicit_host_and_port_accepted(self):
        result = _parse_optional_http_url("http://rtvi:8000")
        assert result is not None
        assert result.hostname == "rtvi"
        assert result.port == 8000

    def test_hostname_only_accepted(self):
        # URL relying on scheme's default port — must not be mis-classified
        # as "not configured" (this is the whole reason the previous narrow
        # guard was replaced).
        result = _parse_optional_http_url("http://rtvi.example.com")
        assert result is not None
        assert result.hostname == "rtvi.example.com"


class TestVideoUploadCompleteInput:
    """Tests for the Pydantic model backing POST /complete.

    These tests lock in the three flags that define the model:
      - alias="sensorId" so VST's camelCase field name is accepted
      - populate_by_name=True so snake_case sensor_id still validates
      - extra="ignore" so forwarding the full VST upload response works
    A future Pydantic bump silently changing any of these would break
    the chunked-upload contract, so pin the behavior here.
    """

    def test_camelcase_sensor_id_accepted(self):
        """VST's raw response uses camelCase; the UI forwards it verbatim."""
        model = VideoUploadCompleteInput(**{"sensorId": "sensor-abc"})
        assert model.sensor_id == "sensor-abc"

    def test_snake_case_sensor_id_accepted(self):
        """populate_by_name keeps the snake_case form valid for back-compat."""
        model = VideoUploadCompleteInput(sensor_id="sensor-abc")
        assert model.sensor_id == "sensor-abc"

    def test_extra_fields_from_full_vst_response_ignored(self):
        """The UI forwards the full ~9-field VST response; we take what we need."""
        full_vst_response = {
            "sensorId": "sensor-1",
            "bytes": 1024,
            "chunkCount": "3",
            "chunkIdentifier": "abc-def",
            "filename": "clip",
            "filePath": "/home/vst/vst_release/streamer_videos/clip.mp4",
            "id": "c66efaeb-40f4-4ef0-9bbf-c06f0c3530ca",
            "streamId": "sensor-1",
            "created_at": "2026-04-23T02:53:04.498Z",
        }
        model = VideoUploadCompleteInput(**full_vst_response)
        assert model.sensor_id == "sensor-1"

    def test_missing_sensor_id_rejected(self):
        with pytest.raises(ValidationError):
            VideoUploadCompleteInput()

    def test_empty_sensor_id_rejected_by_min_length(self):
        """Empty string must fail at the boundary with a clean 422, not
        silently propagate into downstream VST calls where it surfaces as
        a confusing 502 (storage URL .../storage/file//url)."""
        with pytest.raises(ValidationError, match=r"min_length|at least 1"):
            VideoUploadCompleteInput(**{"sensorId": ""})


class TestRunPostUploadProcessing:
    """Tests for the _run_post_upload_processing helper.

    Locks in the graceful-degradation behavior that the chunked-upload
    refactor relies on (Zac's review feedback on PR #127).
    """

    @staticmethod
    def _timeline_patch(start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:10.000Z"):
        return patch(
            "vss_agents.api.video_search_ingest.get_timeline",
            new=AsyncMock(return_value=(start, end)),
        )

    @staticmethod
    def _mock_response(status_code=200, json_body=None, text="OK"):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_body or {}
        resp.text = text
        return resp

    @staticmethod
    def _mock_client(responses):
        """Return an AsyncMock httpx.AsyncClient whose GET/POST yield the given responses in order."""
        client = MagicMock()
        # AsyncClient is used as a context manager in the helper.
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(side_effect=[r for r in responses if r["method"] == "GET"] or [])
        client.post = AsyncMock(side_effect=[r for r in responses if r["method"] == "POST"] or [])
        # Unwrap: callers passed full (method, response) dicts; extract just the responses.
        client.get.side_effect = (r["response"] for r in responses if r["method"] == "GET")
        client.post.side_effect = (r["response"] for r in responses if r["method"] == "POST")
        return client

    @pytest.mark.asyncio
    async def test_happy_path_with_cv_and_embed_configured(self):
        """All services configured → timeline + storage + CV + embed → success message."""
        storage_resp = self._mock_response(200, {"videoUrl": "http://vst/vst/storage/temp_files/clip.mp4"})
        cv_resp = self._mock_response(200, {"ok": True})
        embed_resp = self._mock_response(200, {"usage": {"total_chunks_processed": 42}})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=storage_resp)
        client.post = AsyncMock(side_effect=[cv_resp, embed_resp])

        with self._timeline_patch(), patch("vss_agents.api.video_search_ingest.httpx.AsyncClient", return_value=client):
            result = await _run_post_upload_processing(
                camera_name="clip",
                sensor_id="sensor-abc",
                filename="clip.mp4",
                vst_url="http://vst:30888",
                rtvi_embed_base_url="http://rtvi-embed:8017",
                rtvi_cv_base_url="http://rtvi-cv:9000",
            )

        assert result.video_id == "sensor-abc"
        assert result.chunks_processed == 42
        assert "embeddings generated" in result.message

    @pytest.mark.asyncio
    async def test_rtvi_cv_unreachable_is_skipped_not_fatal(self):
        """If RTVI-CV ConnectError's, log-and-skip, continue to embed, return 200-equivalent."""
        import httpx

        storage_resp = self._mock_response(200, {"videoUrl": "http://vst/vst/storage/temp_files/clip.mp4"})
        embed_resp = self._mock_response(200, {"usage": {"total_chunks_processed": 5}})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=storage_resp)
        # First POST (CV) raises ConnectError; second POST (embed) succeeds.
        client.post = AsyncMock(side_effect=[httpx.ConnectError("connection refused"), embed_resp])

        with self._timeline_patch(), patch("vss_agents.api.video_search_ingest.httpx.AsyncClient", return_value=client):
            result = await _run_post_upload_processing(
                camera_name="clip",
                sensor_id="sensor-abc",
                filename="clip.mp4",
                vst_url="http://vst:30888",
                rtvi_embed_base_url="http://rtvi-embed:8017",
                rtvi_cv_base_url="http://rtvi-cv:9000",
            )

        assert result.chunks_processed == 5

    @pytest.mark.asyncio
    async def test_embed_not_configured_skips_embeddings(self):
        """Empty rtvi_embed_base_url → skip embed, return uploaded-only message."""
        storage_resp = self._mock_response(200, {"videoUrl": "http://vst/vst/storage/temp_files/clip.mp4"})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=storage_resp)
        client.post = AsyncMock()  # No CV or embed POSTs expected.

        with self._timeline_patch(), patch("vss_agents.api.video_search_ingest.httpx.AsyncClient", return_value=client):
            result = await _run_post_upload_processing(
                camera_name="clip",
                sensor_id="sensor-abc",
                filename="clip.mp4",
                vst_url="http://vst:30888",
                rtvi_embed_base_url="",
                rtvi_cv_base_url="",
            )

        assert result.chunks_processed == 0
        assert "embeddings generated" not in result.message
        assert client.post.call_count == 0

    @pytest.mark.asyncio
    async def test_storage_api_missing_video_url_is_502(self):
        """VST returned a response but without `videoUrl` → surface as 502 not silent success."""
        storage_resp = self._mock_response(200, {"unexpected": "shape"})  # no videoUrl key

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=storage_resp)
        client.post = AsyncMock()

        with self._timeline_patch(), patch("vss_agents.api.video_search_ingest.httpx.AsyncClient", return_value=client):
            with pytest.raises(HTTPException) as exc_info:
                await _run_post_upload_processing(
                    camera_name="clip",
                    sensor_id="sensor-abc",
                    filename="clip.mp4",
                    vst_url="http://vst:30888",
                    rtvi_embed_base_url="http://rtvi-embed:8017",
                )
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_invalid_vst_url_is_500(self):
        """vst_url that urlparses without a hostname → 500 (misconfiguration, not transient).

        The helper wraps the input as ``http://{vst_url}`` when the input lacks
        a scheme, so an empty string becomes ``http://`` — urlparse returns
        hostname=None and the helper raises 500.
        """
        storage_resp = self._mock_response(200, {"videoUrl": "http://vst/vst/storage/temp_files/clip.mp4"})
        cv_resp = self._mock_response(200, {"ok": True})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=storage_resp)
        client.post = AsyncMock(return_value=cv_resp)

        with self._timeline_patch(), patch("vss_agents.api.video_search_ingest.httpx.AsyncClient", return_value=client):
            with pytest.raises(HTTPException) as exc_info:
                await _run_post_upload_processing(
                    camera_name="clip",
                    sensor_id="sensor-abc",
                    filename="clip.mp4",
                    vst_url="",  # wraps to "http://" → urlparse hostname=None → 500
                    rtvi_embed_base_url="http://rtvi-embed:8017",
                )
        assert exc_info.value.status_code == 500

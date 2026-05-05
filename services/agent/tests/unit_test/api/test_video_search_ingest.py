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
"""Unit tests for the deprecated /api/v1/videos-for-search/* routes.

Moved tests for shared helpers (_run_post_upload_processing, the response
models, _parse_optional_http_url) live in test_videos.py — they back the
universal upload-complete flow now.
"""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import patch

from fastapi import HTTPException
import pytest

from vss_agents.api.video_search_ingest import ALLOWED_VIDEO_TYPES
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
        assert len(router.routes) > 0

    def test_routes_marked_deprecated_in_schema(self):
        """Both /videos-for-search/* routes are kept for backward compat but
        flagged deprecated in the OpenAPI schema so docs nudge new callers
        toward /api/v1/videos/{filename}/upload-complete."""
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )
        assert all(r.deprecated is True for r in router.routes)


class TestUploadVideoToVstEndpoint:
    """Test upload_video_to_vst endpoint logic."""

    @pytest.mark.asyncio
    async def test_successful_upload(self):
        """The streamed PUT path delegates to the shared post-processing
        helper in vss_agents.api.videos. Both modules ``import httpx`` so
        patching the global ``httpx.AsyncClient`` once covers PUT (in
        video_search_ingest) and GET/POST (in videos). ``get_timeline`` is
        patched on its real home (videos)."""
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )

        mock_request = MagicMock()
        mock_request.headers = {"content-type": "video/mp4", "content-length": "1024"}
        mock_request.stream = AsyncMock(return_value=iter([b"test data"]))

        with (
            patch("vss_agents.api.video_search_ingest.httpx.AsyncClient") as mock_client_class,
            patch("vss_agents.api.videos.get_timeline", new_callable=AsyncMock) as mock_get_timeline,
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
            mock_embed_response.json = Mock(return_value={"usage": {"total_chunks_processed": 5}})

            mock_client.put.return_value = mock_vst_response
            mock_client.get.return_value = mock_storage_response
            mock_client.post.return_value = mock_embed_response

            endpoint = router.routes[0].endpoint
            response = await endpoint(filename="test.mp4", request=mock_request)

            assert response.video_id == "sensor-123"
            assert response.chunks_processed == 5
            assert "successfully uploaded" in response.message

    @pytest.mark.asyncio
    async def test_missing_content_type(self):
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
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )

        mock_request = MagicMock()
        mock_request.headers = {"content-type": "video/webm", "content-length": "1024"}

        endpoint = router.routes[0].endpoint

        with pytest.raises(HTTPException) as exc_info:
            await endpoint(filename="test.mp4", request=mock_request)

        assert exc_info.value.status_code == 415
        assert "Unsupported video format" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_missing_content_length(self):
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
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )

        mock_request = MagicMock()
        mock_request.headers = {"content-type": "video/mp4", "content-length": "1024"}
        mock_request.stream = AsyncMock(return_value=iter([b"test data"]))

        with patch("vss_agents.api.video_search_ingest.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

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
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )

        mock_request = MagicMock()
        mock_request.headers = {"content-type": "video/mp4", "content-length": "1024"}
        mock_request.stream = AsyncMock(return_value=iter([b"test data"]))

        with (
            patch("vss_agents.api.video_search_ingest.httpx.AsyncClient") as mock_client_class,
            patch("vss_agents.api.videos.get_timeline", new_callable=AsyncMock) as mock_get_timeline,
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
    """Test register_video_search_ingest_routes function."""

    def test_register_with_config(self):
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
        mock_app = MagicMock()
        mock_config = MagicMock()
        mock_config.general.front_end = MagicMock(spec=[])

        with pytest.raises(ValueError, match="streaming_ingest"):
            register_video_search_ingest_routes(mock_app, mock_config)

    def test_register_missing_vst_url_raises(self):
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

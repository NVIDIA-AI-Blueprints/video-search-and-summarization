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

import os
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import patch

from fastapi import HTTPException
import pytest

from vss_agents.api.video_search_ingest import ALLOWED_VIDEO_TYPES
from vss_agents.api.video_search_ingest import VideoIngestResponse
from vss_agents.api.video_search_ingest import create_streaming_video_ingest_router
from vss_agents.api.video_search_ingest import register_streaming_routes


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


class TestCreateStreamingVideoIngestRouter:
    """Test create_streaming_video_ingest_router function."""

    def test_create_router(self):
        router = create_streaming_video_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )
        assert router is not None

    def test_create_router_custom_params(self):
        router = create_streaming_video_ingest_router(
            vst_internal_url="http://vst:8080",
            rtvi_embed_base_url="http://rtvi:8080",
            rtvi_embed_model="custom-model",
            rtvi_embed_chunk_duration=10,
        )
        assert router is not None

    def test_router_has_routes(self):
        router = create_streaming_video_ingest_router(
            vst_internal_url="http://vst:8080", rtvi_embed_base_url="http://rtvi:8080"
        )
        # Router should have routes registered
        assert len(router.routes) > 0


class TestStreamVideoToVstEndpoint:
    """Test stream_video_to_vst endpoint logic."""

    @pytest.mark.asyncio
    async def test_successful_upload(self):
        """Test successful video upload flow."""
        router = create_streaming_video_ingest_router(
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
        router = create_streaming_video_ingest_router(
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
        router = create_streaming_video_ingest_router(
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
        router = create_streaming_video_ingest_router(
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
        router = create_streaming_video_ingest_router(
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
        router = create_streaming_video_ingest_router(
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
        router = create_streaming_video_ingest_router(
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
        router = create_streaming_video_ingest_router(
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


class TestRegisterStreamingRoutes:
    """Test register_streaming_routes function."""

    def test_register_with_env_vars(self):
        """Test registering routes using environment variables."""
        mock_app = MagicMock()
        mock_config = MagicMock()
        mock_config.general.front_end = MagicMock(spec=[])  # No streaming_ingest attribute

        with patch.dict(
            os.environ, {"VST_INTERNAL_URL": "http://vst:8080", "HOST_IP": "127.0.0.1", "RTVI_EMBED_PORT": "8017"}
        ):
            register_streaming_routes(mock_app, mock_config)

            # Should call include_router once
            assert mock_app.include_router.called

    def test_register_with_config(self):
        """Test registering routes using config object."""
        mock_app = MagicMock()
        mock_config = MagicMock()

        mock_streaming_config = MagicMock()
        mock_streaming_config.vst_internal_url = "http://vst:8080"
        mock_streaming_config.rtvi_embed_base_url = "http://rtvi:8080"
        mock_streaming_config.rtvi_embed_model = "test-model"
        mock_streaming_config.rtvi_embed_chunk_duration = 10

        mock_config.general.front_end.streaming_ingest = mock_streaming_config

        register_streaming_routes(mock_app, mock_config)

        assert mock_app.include_router.called

    def test_register_missing_vst_url(self):
        """Test error when VST_INTERNAL_URL is not set."""
        mock_app = MagicMock()
        mock_config = MagicMock()
        mock_config.general.front_end = MagicMock(spec=[])

        with patch.dict(os.environ, {}, clear=True), pytest.raises(ValueError, match="VST_INTERNAL_URL"):
            register_streaming_routes(mock_app, mock_config)

    def test_register_missing_rtvi_url(self):
        """Test error when RTVI URL is not configured."""
        mock_app = MagicMock()
        mock_config = MagicMock()
        mock_config.general.front_end = MagicMock(spec=[])

        with patch.dict(os.environ, {"VST_INTERNAL_URL": "http://vst:8080"}, clear=True):
            with pytest.raises(ValueError, match="HOST_IP and RTVI_EMBED_PORT"):
                register_streaming_routes(mock_app, mock_config)

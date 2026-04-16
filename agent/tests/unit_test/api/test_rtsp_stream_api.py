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
"""Unit tests for rtsp_stream_api module."""

import os
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_agents.api.rtsp_stream_api import AddStreamRequest
from vss_agents.api.rtsp_stream_api import AddStreamResponse
from vss_agents.api.rtsp_stream_api import DeleteStreamResponse
from vss_agents.api.rtsp_stream_api import ServiceConfig
from vss_agents.api.rtsp_stream_api import StreamMode
from vss_agents.api.rtsp_stream_api import add_to_rtvi_cv
from vss_agents.api.rtsp_stream_api import add_to_rtvi_embed
from vss_agents.api.rtsp_stream_api import add_to_vst
from vss_agents.api.rtsp_stream_api import cleanup_rtvi_cv
from vss_agents.api.rtsp_stream_api import cleanup_rtvi_embed_generation
from vss_agents.api.rtsp_stream_api import cleanup_rtvi_embed_stream
from vss_agents.api.rtsp_stream_api import cleanup_vst_sensor
from vss_agents.api.rtsp_stream_api import cleanup_vst_storage
from vss_agents.api.rtsp_stream_api import create_rtsp_stream_api_router
from vss_agents.api.rtsp_stream_api import get_stream_info_by_name
from vss_agents.api.rtsp_stream_api import register_rtsp_stream_api_routes
from vss_agents.api.rtsp_stream_api import start_embedding_generation


class TestStreamMode:
    """Test StreamMode enum."""

    def test_search_mode(self):
        assert StreamMode.SEARCH.value == "search"

    def test_other_mode(self):
        assert StreamMode.OTHER.value == "other"

    def test_from_string(self):
        assert StreamMode("search") == StreamMode.SEARCH
        assert StreamMode("other") == StreamMode.OTHER


class TestServiceConfig:
    """Test ServiceConfig class."""

    def test_basic_config(self):
        config = ServiceConfig(vst_internal_url="http://vst:30888")
        assert config.vst_url == "http://vst:30888"
        assert config.rtvi_cv_url == ""
        assert config.rtvi_embed_url == ""
        assert config.rtvi_embed_model == "cosmos-embed1-448p"
        assert config.rtvi_embed_chunk_duration == 5
        assert config.default_stream_mode == StreamMode.SEARCH

    def test_full_config(self):
        config = ServiceConfig(
            vst_internal_url="http://vst:30888/",
            rtvi_cv_base_url="http://rtvi-cv:9000/",
            rtvi_embed_base_url="http://rtvi-embed:8017/",
            rtvi_embed_model="custom-model",
            rtvi_embed_chunk_duration=10,
            default_stream_mode="other",
        )
        assert config.vst_url == "http://vst:30888"
        assert config.rtvi_cv_url == "http://rtvi-cv:9000"
        assert config.rtvi_embed_url == "http://rtvi-embed:8017"
        assert config.rtvi_embed_model == "custom-model"
        assert config.rtvi_embed_chunk_duration == 10
        assert config.default_stream_mode == StreamMode.OTHER


class TestAddStreamRequest:
    """Test AddStreamRequest model."""

    def test_required_fields(self):
        request = AddStreamRequest(sensor_url="rtsp://camera:554/stream", name="camera-1")
        assert request.sensor_url == "rtsp://camera:554/stream"
        assert request.name == "camera-1"
        assert request.username == ""
        assert request.password == ""
        assert request.location == ""
        assert request.tags == ""

    def test_all_fields(self):
        request = AddStreamRequest(
            sensor_url="rtsp://camera:554/stream",
            name="camera-1",
            username="admin",
            password="pw",  # pragma: allowlist secret
            location="Building A",
            tags="entrance,security",
        )
        assert request.username == "admin"
        assert request.password == "pw"  # pragma: allowlist secret
        assert request.location == "Building A"
        assert request.tags == "entrance,security"

    def test_alias_sensor_url(self):
        """Test that sensorUrl alias works."""
        request = AddStreamRequest(sensorUrl="rtsp://camera:554/stream", name="camera-1")
        assert request.sensor_url == "rtsp://camera:554/stream"

    def test_missing_required_fields_fails(self):
        with pytest.raises(Exception):
            AddStreamRequest(name="camera-1")  # Missing sensor_url


class TestAddStreamResponse:
    """Test AddStreamResponse model."""

    def test_success_response(self):
        response = AddStreamResponse(status="success", message="Stream added successfully")
        assert response.status == "success"
        assert response.message == "Stream added successfully"
        assert response.error is None

    def test_failure_response(self):
        response = AddStreamResponse(status="failure", message="Failed to add stream", error="VST error")
        assert response.status == "failure"
        assert response.error == "VST error"


class TestDeleteStreamResponse:
    """Test DeleteStreamResponse model."""

    def test_success_response(self):
        response = DeleteStreamResponse(status="success", message="Stream deleted", name="camera-1")
        assert response.status == "success"
        assert response.name == "camera-1"

    def test_partial_response(self):
        response = DeleteStreamResponse(status="partial", message="Partially deleted", name="camera-1")
        assert response.status == "partial"


class TestAddToVst:
    """Test add_to_vst function."""

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_stream_api.vst_add_sensor")
    @patch("vss_agents.api.rtsp_stream_api.vst_get_rtsp_url")
    async def test_successful_add(self, mock_get_rtsp_url, mock_add_sensor):
        config = ServiceConfig(vst_internal_url="http://vst:30888")
        request = AddStreamRequest(sensor_url="rtsp://camera:554/stream", name="camera-1")

        # Mock VST add sensor
        mock_add_sensor.return_value = (True, "OK", "sensor-123")
        # Mock VST get RTSP URL
        mock_get_rtsp_url.return_value = (True, "OK", "rtsp://vst:554/sensor-123")

        success, _msg, sensor_id, rtsp_url = await add_to_vst(config, request)

        assert success is True
        assert sensor_id == "sensor-123"
        assert rtsp_url == "rtsp://vst:554/sensor-123"

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_stream_api.vst_add_sensor")
    async def test_vst_returns_error(self, mock_add_sensor):
        config = ServiceConfig(vst_internal_url="http://vst:30888")
        request = AddStreamRequest(sensor_url="rtsp://camera:554/stream", name="camera-1")

        mock_add_sensor.return_value = (False, "VST returned 500: Internal Server Error", None)

        success, msg, sensor_id, _rtsp_url = await add_to_vst(config, request)

        assert success is False
        assert "500" in msg
        assert sensor_id is None

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_stream_api.vst_add_sensor")
    async def test_vst_missing_sensor_id(self, mock_add_sensor):
        config = ServiceConfig(vst_internal_url="http://vst:30888")
        request = AddStreamRequest(sensor_url="rtsp://camera:554/stream", name="camera-1")

        mock_add_sensor.return_value = (False, "VST response missing sensor ID: {}", None)

        success, msg, _sensor_id, _rtsp_url = await add_to_vst(config, request)

        assert success is False
        assert "missing sensor ID" in msg


class TestAddToRtviCv:
    """Test add_to_rtvi_cv function."""

    @pytest.mark.asyncio
    async def test_successful_add(self):
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_cv_base_url="http://rtvi-cv:9000")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_response)

        success, msg = await add_to_rtvi_cv(mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123")

        assert success is True
        assert msg == "OK"

    @pytest.mark.asyncio
    async def test_skipped_when_not_configured(self):
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_cv_base_url="")

        success, msg = await add_to_rtvi_cv(mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123")

        assert success is True
        assert "Skipped" in msg
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_rtvi_cv_error(self):
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_cv_base_url="http://rtvi-cv:9000")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Error"
        mock_client.post = AsyncMock(return_value=mock_response)

        success, msg = await add_to_rtvi_cv(mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123")

        assert success is False
        assert "500" in msg


class TestAddToRtviEmbed:
    """Test add_to_rtvi_embed function."""

    @pytest.mark.asyncio
    async def test_successful_add(self):
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={"streams": [{"id": "rtvi-stream-123"}]})
        mock_client.post = AsyncMock(return_value=mock_response)

        success, _msg, stream_id = await add_to_rtvi_embed(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is True
        assert stream_id == "rtvi-stream-123"

    @pytest.mark.asyncio
    async def test_skipped_when_not_configured(self):
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="")

        success, _msg, stream_id = await add_to_rtvi_embed(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is True
        assert "Skipped" in _msg
        assert stream_id == "sensor-123"  # Falls back to sensor_id

    @pytest.mark.asyncio
    async def test_fallback_to_sensor_id(self):
        """Test that stream_id falls back to sensor_id when not in response."""
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={"streams": []})  # Empty streams
        mock_client.post = AsyncMock(return_value=mock_response)

        success, _msg, stream_id = await add_to_rtvi_embed(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is True
        assert stream_id == "sensor-123"


class TestStartEmbeddingGeneration:
    """Test start_embedding_generation function."""

    @pytest.mark.asyncio
    async def test_successful_start(self):
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        # Create mock response for streaming context manager
        mock_response = MagicMock()
        mock_response.status_code = 200

        # Create stream context manager
        mock_stream_cm = MagicMock()
        mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=None)

        # Create mock client with stream method
        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_stream_cm)

        success, msg = await start_embedding_generation(mock_client, config, "stream-123")

        assert success is True
        assert msg == "OK"

    @pytest.mark.asyncio
    async def test_skipped_when_not_configured(self):
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="")
        mock_client = MagicMock()

        success, msg = await start_embedding_generation(mock_client, config, "stream-123")

        assert success is True
        assert "Skipped" in msg


class TestGetStreamInfoByName:
    """Test get_stream_info_by_name function."""

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_stream_api.vst_get_stream_info_by_name")
    async def test_successful_lookup(self, mock_vst_get_stream_info):
        config = ServiceConfig(vst_internal_url="http://vst:30888")

        mock_vst_get_stream_info.return_value = ("sensor-123", "rtsp://vst:554/sensor-123")

        success, _msg, stream_id, rtsp_url = await get_stream_info_by_name(config, "camera-1")

        assert success is True
        assert stream_id == "sensor-123"
        assert rtsp_url == "rtsp://vst:554/sensor-123"

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_stream_api.vst_get_stream_info_by_name")
    async def test_name_not_found(self, mock_vst_get_stream_info):
        config = ServiceConfig(vst_internal_url="http://vst:30888")

        mock_vst_get_stream_info.return_value = (None, None)

        success, msg, _stream_id, _rtsp_url = await get_stream_info_by_name(config, "camera-1")

        assert success is False
        assert "not found" in msg


class TestCleanupFunctions:
    """Test cleanup functions."""

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_stream_api.vst_delete_sensor")
    async def test_cleanup_vst_sensor_success(self, mock_vst_delete_sensor):
        config = ServiceConfig(vst_internal_url="http://vst:30888")

        mock_vst_delete_sensor.return_value = (True, "OK")

        success, _msg = await cleanup_vst_sensor(config, "sensor-123")

        assert success is True

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_stream_api.vst_delete_storage")
    async def test_cleanup_vst_storage_no_timeline(self, mock_vst_delete_storage):
        config = ServiceConfig(vst_internal_url="http://vst:30888")

        mock_vst_delete_storage.return_value = (True, "No storage to delete")

        success, msg = await cleanup_vst_storage(config, "sensor-123")

        assert success is True
        assert "No storage to delete" in msg

    @pytest.mark.asyncio
    async def test_cleanup_rtvi_cv_skipped(self):
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_cv_base_url="")

        success, msg = await cleanup_rtvi_cv(mock_client, config, "sensor-123")

        assert success is True
        assert "Skipped" in msg

    @pytest.mark.asyncio
    async def test_cleanup_rtvi_embed_stream_success(self):
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.delete = AsyncMock(return_value=mock_response)

        success, _msg = await cleanup_rtvi_embed_stream(mock_client, config, "stream-123")

        assert success is True

    @pytest.mark.asyncio
    async def test_cleanup_rtvi_embed_generation_success(self):
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.delete = AsyncMock(return_value=mock_response)

        success, _msg = await cleanup_rtvi_embed_generation(mock_client, config, "stream-123")

        assert success is True


class TestCreateRtspStreamApiRouter:
    """Test create_rtsp_stream_api_router function."""

    def test_create_router(self):
        router = create_rtsp_stream_api_router(vst_internal_url="http://vst:30888")
        assert router is not None

    def test_create_router_with_all_params(self):
        router = create_rtsp_stream_api_router(
            vst_internal_url="http://vst:30888",
            rtvi_cv_base_url="http://rtvi-cv:9000",
            rtvi_embed_base_url="http://rtvi-embed:8017",
            rtvi_embed_model="custom-model",
            rtvi_embed_chunk_duration=10,
            default_stream_mode="other",
        )
        assert router is not None

    def test_router_has_routes(self):
        router = create_rtsp_stream_api_router(vst_internal_url="http://vst:30888")
        assert len(router.routes) == 2  # add and delete endpoints


class TestAddStreamEndpoint:
    """Test add_stream endpoint."""

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_stream_api.start_embedding_generation")
    @patch("vss_agents.api.rtsp_stream_api.add_to_rtvi_embed")
    @patch("vss_agents.api.rtsp_stream_api.add_to_rtvi_cv")
    @patch("vss_agents.api.rtsp_stream_api.add_to_vst")
    @patch("vss_agents.api.rtsp_stream_api.httpx.AsyncClient")
    async def test_successful_add_search_mode(
        self, mock_client_class, mock_add_vst, mock_add_rtvi_cv, mock_add_rtvi_embed, mock_start_embed
    ):
        """Test successful stream addition in search mode."""
        router = create_rtsp_stream_api_router(
            vst_internal_url="http://vst:30888",
            rtvi_cv_base_url="http://rtvi-cv:9000",
            rtvi_embed_base_url="http://rtvi-embed:8017",
            default_stream_mode="search",
        )

        # Mock httpx client
        mock_client = MagicMock()
        mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

        # Mock all helper functions
        mock_add_vst.return_value = (True, "OK", "sensor-123", "rtsp://vst:554/sensor-123")
        mock_add_rtvi_cv.return_value = (True, "OK")
        mock_add_rtvi_embed.return_value = (True, "OK", "sensor-123")
        mock_start_embed.return_value = (True, "OK")

        # Get endpoint and call
        endpoint = router.routes[0].endpoint
        request = AddStreamRequest(sensor_url="rtsp://camera:554/stream", name="camera-1")
        response = await endpoint(request)

        assert response.status == "success"
        assert "camera-1" in response.message

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_stream_api.add_to_vst")
    async def test_successful_add_other_mode(self, mock_add_vst):
        """Test successful stream addition in 'other' mode (VST only)."""
        router = create_rtsp_stream_api_router(
            vst_internal_url="http://vst:30888",
            default_stream_mode="other",
        )

        # Mock VST add
        mock_add_vst.return_value = (True, "OK", "sensor-123", "rtsp://vst:554/sensor-123")

        endpoint = router.routes[0].endpoint
        request = AddStreamRequest(sensor_url="rtsp://camera:554/stream", name="camera-1")
        response = await endpoint(request)

        assert response.status == "success"

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_stream_api.add_to_vst")
    async def test_vst_failure_no_rollback_needed(self, mock_add_vst):
        """Test that VST failure doesn't trigger rollback (nothing to rollback)."""
        router = create_rtsp_stream_api_router(
            vst_internal_url="http://vst:30888",
            default_stream_mode="search",
        )

        mock_add_vst.return_value = (False, "VST returned 500: Server error", None, None)

        endpoint = router.routes[0].endpoint
        request = AddStreamRequest(sensor_url="rtsp://camera:554/stream", name="camera-1")
        response = await endpoint(request)

        assert response.status == "failure"
        assert "VST" in response.message

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_stream_api.cleanup_vst_storage")
    @patch("vss_agents.api.rtsp_stream_api.cleanup_vst_sensor")
    @patch("vss_agents.api.rtsp_stream_api.add_to_rtvi_cv")
    @patch("vss_agents.api.rtsp_stream_api.add_to_vst")
    @patch("vss_agents.api.rtsp_stream_api.httpx.AsyncClient")
    async def test_rtvi_cv_failure_triggers_rollback(
        self, mock_client_class, mock_add_vst, mock_add_rtvi_cv, mock_cleanup_sensor, mock_cleanup_storage
    ):
        """Test that RTVI-CV failure triggers VST cleanup."""
        router = create_rtsp_stream_api_router(
            vst_internal_url="http://vst:30888",
            rtvi_cv_base_url="http://rtvi-cv:9000",
            rtvi_embed_base_url="http://rtvi-embed:8017",
            default_stream_mode="search",
        )

        # Mock httpx client
        mock_client = MagicMock()
        mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

        # VST success, RTVI-CV failure
        mock_add_vst.return_value = (True, "OK", "sensor-123", "rtsp://vst:554/sensor-123")
        mock_add_rtvi_cv.return_value = (False, "RTVI-CV error")
        mock_cleanup_sensor.return_value = (True, "OK")
        mock_cleanup_storage.return_value = (True, "OK")

        endpoint = router.routes[0].endpoint
        request = AddStreamRequest(sensor_url="rtsp://camera:554/stream", name="camera-1")
        response = await endpoint(request)

        assert response.status == "failure"
        assert "RTVI-CV" in response.message
        # Should have called cleanup functions
        mock_cleanup_sensor.assert_called_once()
        mock_cleanup_storage.assert_called_once()


class TestDeleteStreamEndpoint:
    """Test delete_stream endpoint."""

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_stream_api.cleanup_vst_sensor")
    @patch("vss_agents.api.rtsp_stream_api.cleanup_rtvi_cv")
    @patch("vss_agents.api.rtsp_stream_api.cleanup_rtvi_embed_stream")
    @patch("vss_agents.api.rtsp_stream_api.cleanup_rtvi_embed_generation")
    @patch("vss_agents.api.rtsp_stream_api.get_stream_info_by_name")
    @patch("vss_agents.api.rtsp_stream_api.httpx.AsyncClient")
    async def test_successful_delete_search_mode(
        self,
        mock_client_class,
        mock_get_stream_info,
        mock_cleanup_embed_gen,
        mock_cleanup_embed_stream,
        mock_cleanup_rtvi_cv,
        mock_cleanup_vst_sensor,
    ):
        """Test successful stream deletion in search mode."""
        router = create_rtsp_stream_api_router(
            vst_internal_url="http://vst:30888",
            rtvi_cv_base_url="http://rtvi-cv:9000",
            rtvi_embed_base_url="http://rtvi-embed:8017",
            default_stream_mode="search",
        )

        # Mock httpx client
        mock_client = MagicMock()
        mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

        # Mock all helper functions
        mock_get_stream_info.return_value = (True, "OK", "sensor-123", "rtsp://vst:554/sensor-123")
        mock_cleanup_embed_gen.return_value = (True, "OK")
        mock_cleanup_embed_stream.return_value = (True, "OK")
        mock_cleanup_rtvi_cv.return_value = (True, "OK")
        mock_cleanup_vst_sensor.return_value = (True, "OK")

        endpoint = router.routes[1].endpoint
        response = await endpoint(name="camera-1")

        assert response.status == "success"
        assert response.name == "camera-1"

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_stream_api.get_stream_info_by_name")
    async def test_delete_stream_not_found(self, mock_get_stream_info):
        """Test deletion when stream is not found."""
        router = create_rtsp_stream_api_router(
            vst_internal_url="http://vst:30888",
            default_stream_mode="search",
        )

        mock_get_stream_info.return_value = (False, "Stream not found", None, None)

        endpoint = router.routes[1].endpoint
        response = await endpoint(name="nonexistent-camera")

        assert response.status == "failure"
        assert "not found" in response.message.lower() or "Failed to find" in response.message

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_stream_api.cleanup_vst_sensor")
    @patch("vss_agents.api.rtsp_stream_api.cleanup_rtvi_cv")
    @patch("vss_agents.api.rtsp_stream_api.cleanup_rtvi_embed_stream")
    @patch("vss_agents.api.rtsp_stream_api.cleanup_rtvi_embed_generation")
    @patch("vss_agents.api.rtsp_stream_api.get_stream_info_by_name")
    @patch("vss_agents.api.rtsp_stream_api.httpx.AsyncClient")
    async def test_partial_delete(
        self,
        mock_client_class,
        mock_get_stream_info,
        mock_cleanup_embed_gen,
        mock_cleanup_embed_stream,
        mock_cleanup_rtvi_cv,
        mock_cleanup_vst_sensor,
    ):
        """Test partial deletion when some services fail."""
        router = create_rtsp_stream_api_router(
            vst_internal_url="http://vst:30888",
            rtvi_cv_base_url="http://rtvi-cv:9000",
            rtvi_embed_base_url="http://rtvi-embed:8017",
            default_stream_mode="search",
        )

        # Mock httpx client
        mock_client = MagicMock()
        mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

        # Mock helper functions with mixed success/failure
        mock_get_stream_info.return_value = (True, "OK", "sensor-123", "rtsp://vst:554/sensor-123")
        mock_cleanup_embed_gen.return_value = (True, "OK")
        mock_cleanup_embed_stream.return_value = (False, "Error")  # Failure
        mock_cleanup_rtvi_cv.return_value = (True, "OK")
        mock_cleanup_vst_sensor.return_value = (True, "OK")

        endpoint = router.routes[1].endpoint
        response = await endpoint(name="camera-1")

        assert response.status == "partial"


class TestRegisterRtspStreamApiRoutes:
    """Test register_rtsp_stream_api_routes function."""

    def test_register_with_config(self):
        """Test registering routes using config object."""
        mock_app = MagicMock()
        mock_config = MagicMock()

        mock_streaming_config = MagicMock()
        mock_streaming_config.vst_internal_url = "http://vst:30888"
        mock_streaming_config.rtvi_cv_base_url = "http://rtvi-cv:9000"
        mock_streaming_config.rtvi_embed_base_url = "http://rtvi-embed:8017"
        mock_streaming_config.rtvi_embed_model = "test-model"
        mock_streaming_config.rtvi_embed_chunk_duration = 10
        mock_streaming_config.stream_mode = "search"

        mock_config.general.front_end.streaming_ingest = mock_streaming_config

        register_rtsp_stream_api_routes(mock_app, mock_config)

        assert mock_app.include_router.called

    def test_register_with_env_vars(self):
        """Test registering routes using environment variables."""
        mock_app = MagicMock()
        mock_config = MagicMock()
        mock_config.general.front_end = MagicMock(spec=[])  # No streaming_ingest attribute

        with patch.dict(
            os.environ,
            {
                "VST_INTERNAL_URL": "http://vst:30888",
                "HOST_IP": "127.0.0.1",
                "RTVI_EMBED_PORT": "8017",
            },
        ):
            register_rtsp_stream_api_routes(mock_app, mock_config)

            assert mock_app.include_router.called

    def test_register_missing_vst_url(self):
        """Test error when VST_INTERNAL_URL is not set."""
        mock_app = MagicMock()
        mock_config = MagicMock()
        mock_config.general.front_end = MagicMock(spec=[])

        with patch.dict(os.environ, {}, clear=True), pytest.raises(ValueError, match="VST_INTERNAL_URL"):
            register_rtsp_stream_api_routes(mock_app, mock_config)

    def test_register_missing_rtvi_embed_url(self):
        """Test error when RTVI-embed URL is not configured."""
        mock_app = MagicMock()
        mock_config = MagicMock()
        mock_config.general.front_end = MagicMock(spec=[])

        with patch.dict(os.environ, {"VST_INTERNAL_URL": "http://vst:30888"}, clear=True):
            with pytest.raises(ValueError, match="RTVI-embed"):
                register_rtsp_stream_api_routes(mock_app, mock_config)

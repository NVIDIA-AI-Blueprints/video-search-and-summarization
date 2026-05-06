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
"""Unit tests for rtsp_ingest module."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx
import pytest
from tenacity import AsyncRetrying
from tenacity import retry_if_exception_type
from tenacity import stop_after_attempt
from tenacity import wait_none

from vss_agents.api.rtsp_ingest import AddStreamRequest
from vss_agents.api.rtsp_ingest import AddStreamResponse
from vss_agents.api.rtsp_ingest import ServiceConfig
from vss_agents.api.rtsp_ingest import add_to_rtvi_cv
from vss_agents.api.rtsp_ingest import add_to_rtvi_embed
from vss_agents.api.rtsp_ingest import add_to_rtvi_vlm
from vss_agents.api.rtsp_ingest import add_to_vst
from vss_agents.api.rtsp_ingest import cleanup_rtvi_cv
from vss_agents.api.rtsp_ingest import cleanup_rtvi_embed_generation
from vss_agents.api.rtsp_ingest import cleanup_rtvi_embed_stream
from vss_agents.api.rtsp_ingest import cleanup_rtvi_vlm_stream
from vss_agents.api.rtsp_ingest import cleanup_vst_sensor
from vss_agents.api.rtsp_ingest import cleanup_vst_storage
from vss_agents.api.rtsp_ingest import create_rtsp_ingest_router
from vss_agents.api.rtsp_ingest import get_stream_info_by_name
from vss_agents.api.rtsp_ingest import register_rtsp_ingest_routes
from vss_agents.api.rtsp_ingest import start_embedding_generation


def _single_attempt_retry() -> AsyncRetrying:
    """Return a retry strategy that executes exactly once with no delay (for unit tests)."""
    return AsyncRetrying(stop=stop_after_attempt(1), wait=wait_none(), reraise=True)


def _multi_attempt_retry(attempts: int = 3) -> AsyncRetrying:
    """Return a retry strategy with *attempts* tries, no delay, retrying on any Exception."""
    return AsyncRetrying(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(attempts),
        wait=wait_none(),
        reraise=True,
    )


class TestServiceConfig:
    """Test ServiceConfig class."""

    def test_basic_config(self):
        config = ServiceConfig(vst_internal_url="http://vst:30888")
        assert config.vst_url == "http://vst:30888"
        assert config.rtvi_cv_url == ""
        assert config.rtvi_embed_url == ""
        assert config.rtvi_vlm_url == ""
        assert config.rtvi_embed_model == "cosmos-embed1-448p"
        assert config.rtvi_embed_chunk_duration == 5
        # default: alerts/base/lvs-style behavior — VST owns storage, so delete it on remove
        assert config.delete_vst_storage_on_stream_remove is True

    def test_full_config(self):
        config = ServiceConfig(
            vst_internal_url="http://vst:30888/",
            rtvi_cv_base_url="http://rtvi-cv:9000/",
            rtvi_embed_base_url="http://rtvi-embed:8017/",
            rtvi_vlm_base_url="http://rtvi-vlm:8018/",
            rtvi_embed_model="custom-model",
            rtvi_embed_chunk_duration=10,
            delete_vst_storage_on_stream_remove=False,
        )
        assert config.vst_url == "http://vst:30888"
        assert config.rtvi_cv_url == "http://rtvi-cv:9000"
        assert config.rtvi_embed_url == "http://rtvi-embed:8017"
        assert config.rtvi_vlm_url == "http://rtvi-vlm:8018"
        assert config.rtvi_embed_model == "custom-model"
        assert config.rtvi_embed_chunk_duration == 10
        # search-style: RTVI owns storage lifecycle, leave VST storage alone
        assert config.delete_vst_storage_on_stream_remove is False


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


class TestAddToVst:
    """Test add_to_vst function."""

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_ingest.vst_add_sensor")
    @patch("vss_agents.api.rtsp_ingest.vst_get_rtsp_url")
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
    @patch("vss_agents.api.rtsp_ingest.vst_add_sensor")
    async def test_vst_returns_error(self, mock_add_sensor):
        config = ServiceConfig(vst_internal_url="http://vst:30888")
        request = AddStreamRequest(sensor_url="rtsp://camera:554/stream", name="camera-1")

        mock_add_sensor.return_value = (False, "VST returned 500: Internal Server Error", None)

        success, msg, sensor_id, _rtsp_url = await add_to_vst(config, request)

        assert success is False
        assert "500" in msg
        assert sensor_id is None

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_ingest.vst_add_sensor")
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
    @patch("vss_agents.api.rtsp_ingest.create_retry_strategy")
    async def test_successful_add(self, mock_retry):
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={"streams": [{"id": "rtvi-stream-123"}]})
        mock_client.post = AsyncMock(return_value=mock_response)

        mock_retry.return_value = _single_attempt_retry()

        success, _msg, stream_id = await add_to_rtvi_embed(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is True
        assert stream_id == "rtvi-stream-123"

    @pytest.mark.asyncio
    async def test_skipped_when_not_configured(self):
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="")

        success, msg, stream_id = await add_to_rtvi_embed(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is True
        assert "Skipped" in msg
        assert stream_id == "sensor-123"

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_ingest.create_retry_strategy")
    async def test_fallback_to_sensor_id(self, mock_retry):
        """Test that stream_id falls back to sensor_id when not in response."""
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={"streams": []})
        mock_client.post = AsyncMock(return_value=mock_response)

        mock_retry.return_value = _single_attempt_retry()

        success, _msg, stream_id = await add_to_rtvi_embed(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is True
        assert stream_id == "sensor-123"

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_ingest.create_retry_strategy")
    async def test_retry_succeeds_after_transient_failure(self, mock_retry):
        """Test that a transient 503 followed by 200 succeeds."""
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        fail_response = MagicMock()
        fail_response.status_code = 503
        fail_response.text = "Service Unavailable"

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json = MagicMock(return_value={"streams": [{"id": "rtvi-stream-123"}]})

        mock_client.post = AsyncMock(side_effect=[fail_response, ok_response])

        mock_retry.return_value = _multi_attempt_retry(attempts=3)

        success, _msg, stream_id = await add_to_rtvi_embed(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is True
        assert stream_id == "rtvi-stream-123"
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_ingest.create_retry_strategy")
    async def test_all_retries_exhausted(self, mock_retry):
        """Test that persistent failures return an error after retries are exhausted."""
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        fail_response = MagicMock()
        fail_response.status_code = 500
        fail_response.text = "Internal Server Error"

        mock_client.post = AsyncMock(return_value=fail_response)

        mock_retry.return_value = _multi_attempt_retry(attempts=3)

        success, msg, stream_id = await add_to_rtvi_embed(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is False
        assert "RTVI-embed" in msg
        assert stream_id is None
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_ingest.create_retry_strategy")
    async def test_connection_error_retried(self, mock_retry):
        """Test that network-level exceptions are retried."""
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json = MagicMock(return_value={"streams": [{"id": "rtvi-stream-123"}]})

        mock_client.post = AsyncMock(side_effect=[httpx.ConnectError("connection refused"), ok_response])

        mock_retry.return_value = _multi_attempt_retry(attempts=3)

        success, _msg, stream_id = await add_to_rtvi_embed(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is True
        assert stream_id == "rtvi-stream-123"
        assert mock_client.post.call_count == 2


class TestAddToRtviEmbedRealRetry:
    """Tests that exercise the real create_retry_strategy to pin configured retry parameters."""

    @pytest.mark.asyncio
    @patch("vss_agents.utils.retry.wait_random", return_value=wait_none())
    async def test_retries_on_transport_error(self, _mock_wait):
        """Real retry strategy retries httpx.TransportError and eventually succeeds."""
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json = MagicMock(return_value={"streams": [{"id": "rtvi-stream-123"}]})

        mock_client.post = AsyncMock(
            side_effect=[
                httpx.ConnectError("connection refused"),
                httpx.ConnectError("connection refused"),
                ok_response,
            ]
        )

        success, _msg, stream_id = await add_to_rtvi_embed(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is True
        assert stream_id == "rtvi-stream-123"
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    @patch("vss_agents.utils.retry.wait_random", return_value=wait_none())
    async def test_retries_on_timeout(self, _mock_wait):
        """httpx.TimeoutException (subclass of TransportError) is retried."""
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json = MagicMock(return_value={"streams": [{"id": "rtvi-stream-123"}]})

        mock_client.post = AsyncMock(side_effect=[httpx.ReadTimeout("timed out"), ok_response])

        success, _msg, stream_id = await add_to_rtvi_embed(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is True
        assert stream_id == "rtvi-stream-123"
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    @patch("vss_agents.utils.retry.wait_random", return_value=wait_none())
    async def test_does_not_retry_on_non_retryable_exception(self, _mock_wait):
        """Real retry strategy does NOT retry exceptions outside the configured tuple."""
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        mock_client.post = AsyncMock(side_effect=KeyError("unexpected"))

        success, _msg, stream_id = await add_to_rtvi_embed(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is False
        assert stream_id is None
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    @patch("vss_agents.utils.retry.wait_random", return_value=wait_none())
    async def test_exhausts_all_six_retries_on_server_error(self, _mock_wait):
        """Real retry strategy attempts exactly 6 times before giving up on 500s."""
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        fail_response = MagicMock()
        fail_response.status_code = 500
        fail_response.text = "Internal Server Error"

        mock_client.post = AsyncMock(return_value=fail_response)

        success, _msg, stream_id = await add_to_rtvi_embed(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is False
        assert stream_id is None
        assert mock_client.post.call_count == 6

    @pytest.mark.asyncio
    @patch("vss_agents.utils.retry.wait_random", return_value=wait_none())
    async def test_4xx_not_retried(self, _mock_wait):
        """Real retry strategy returns immediately on 4xx client errors."""
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_embed_base_url="http://rtvi-embed:8017")

        bad_request = MagicMock()
        bad_request.status_code = 400
        bad_request.text = "Bad Request"

        mock_client.post = AsyncMock(return_value=bad_request)

        success, msg, stream_id = await add_to_rtvi_embed(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is False
        assert "400" in msg
        assert stream_id is None
        mock_client.post.assert_called_once()


class TestAddToRtviVlm:
    """Test add_to_rtvi_vlm function."""

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_ingest.create_retry_strategy")
    async def test_successful_add(self, mock_retry):
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_vlm_base_url="http://rtvi-vlm:8018")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"results": [{"id": "sensor-123"}]}'
        mock_response.json = MagicMock(return_value={"results": [{"id": "sensor-123"}]})
        mock_client.post = AsyncMock(return_value=mock_response)

        mock_retry.return_value = _single_attempt_retry()

        success, _msg, stream_id = await add_to_rtvi_vlm(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is True
        assert stream_id == "sensor-123"
        mock_client.post.assert_called_once_with(
            "http://rtvi-vlm:8018/v1/streams/add",
            json={
                "streams": [
                    {
                        "liveStreamUrl": "rtsp://vst:554/sensor-123",
                        "description": "camera-1",
                        "sensor_name": "sensor-123",
                        "id": "sensor-123",
                    }
                ],
            },
        )

    @pytest.mark.asyncio
    async def test_skipped_when_not_configured(self):
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_vlm_base_url="")

        success, msg, stream_id = await add_to_rtvi_vlm(
            mock_client, config, "sensor-123", "camera-1", "rtsp://vst:554/sensor-123"
        )

        assert success is True
        assert "Skipped" in msg
        assert stream_id == "sensor-123"
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_stream_success(self):
        mock_client = MagicMock()
        config = ServiceConfig(vst_internal_url="http://vst:30888", rtvi_vlm_base_url="http://rtvi-vlm:8018")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.delete = AsyncMock(return_value=mock_response)

        success, msg = await cleanup_rtvi_vlm_stream(mock_client, config, "sensor-123")

        assert success is True
        assert msg == "OK"
        mock_client.delete.assert_called_once_with("http://rtvi-vlm:8018/v1/streams/delete/sensor-123")


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
    @patch("vss_agents.api.rtsp_ingest.vst_get_stream_info_by_name")
    async def test_successful_lookup(self, mock_vst_get_stream_info):
        config = ServiceConfig(vst_internal_url="http://vst:30888")

        mock_vst_get_stream_info.return_value = ("sensor-123", "rtsp://vst:554/sensor-123")

        success, _msg, stream_id, rtsp_url = await get_stream_info_by_name(config, "camera-1")

        assert success is True
        assert stream_id == "sensor-123"
        assert rtsp_url == "rtsp://vst:554/sensor-123"

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_ingest.vst_get_stream_info_by_name")
    async def test_name_not_found(self, mock_vst_get_stream_info):
        config = ServiceConfig(vst_internal_url="http://vst:30888")

        mock_vst_get_stream_info.return_value = (None, None)

        success, msg, _stream_id, _rtsp_url = await get_stream_info_by_name(config, "camera-1")

        assert success is False
        assert "not found" in msg


class TestCleanupFunctions:
    """Test cleanup functions."""

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_ingest.vst_delete_sensor")
    async def test_cleanup_vst_sensor_success(self, mock_vst_delete_sensor):
        config = ServiceConfig(vst_internal_url="http://vst:30888")

        mock_vst_delete_sensor.return_value = (True, "OK")

        success, _msg = await cleanup_vst_sensor(config, "sensor-123")

        assert success is True

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_ingest.vst_delete_storage")
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
    """Test create_rtsp_ingest_router function."""

    def test_create_router(self):
        router = create_rtsp_ingest_router(ServiceConfig(vst_internal_url="http://vst:30888"))
        assert router is not None

    def test_create_router_with_all_params(self):
        router = create_rtsp_ingest_router(
            ServiceConfig(
                vst_internal_url="http://vst:30888",
                rtvi_cv_base_url="http://rtvi-cv:9000",
                rtvi_embed_base_url="http://rtvi-embed:8017",
                rtvi_vlm_base_url="http://rtvi-vlm:8018",
                rtvi_embed_model="custom-model",
                rtvi_embed_chunk_duration=10,
                delete_vst_storage_on_stream_remove=True,
            )
        )
        assert router is not None

    def test_router_has_routes(self):
        router = create_rtsp_ingest_router(ServiceConfig(vst_internal_url="http://vst:30888"))
        assert len(router.routes) == 1  # add endpoint only; delete lives in rtsp_delete


class TestAddStreamEndpoint:
    """Test add_stream endpoint."""

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_ingest.start_embedding_generation")
    @patch("vss_agents.api.rtsp_ingest.add_to_rtvi_embed")
    @patch("vss_agents.api.rtsp_ingest.add_to_rtvi_cv")
    @patch("vss_agents.api.rtsp_ingest.add_to_vst")
    @patch("vss_agents.api.rtsp_ingest.httpx.AsyncClient")
    async def test_successful_add_with_full_rtvi(
        self, mock_client_class, mock_add_vst, mock_add_rtvi_cv, mock_add_rtvi_embed, mock_start_embed
    ):
        """Test successful stream addition with RTVI-CV + RTVI-embed configured (search-style)."""
        router = create_rtsp_ingest_router(
            ServiceConfig(
                vst_internal_url="http://vst:30888",
                rtvi_cv_base_url="http://rtvi-cv:9000",
                rtvi_embed_base_url="http://rtvi-embed:8017",
            )
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
    @patch("vss_agents.api.rtsp_ingest.add_to_vst")
    async def test_successful_add_vst_only(self, mock_add_vst):
        """Test successful stream addition with VST only (no RTVI URLs configured).

        With no RTVI URLs, the LVS branch is skipped (``rtvi_vlm_base_url`` is
        empty) and ``add_to_rtvi_cv``/``add_to_rtvi_embed``/
        ``start_embedding_generation`` self-skip — VST add is the only real
        side effect.
        """
        router = create_rtsp_ingest_router(ServiceConfig(vst_internal_url="http://vst:30888"))

        mock_add_vst.return_value = (True, "OK", "sensor-123", "rtsp://vst:554/sensor-123")

        endpoint = router.routes[0].endpoint
        request = AddStreamRequest(sensor_url="rtsp://camera:554/stream", name="camera-1")
        response = await endpoint(request)

        assert response.status == "success"
        assert "camera-1" in response.message

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_ingest.cleanup_vst_storage")
    @patch("vss_agents.api.rtsp_ingest.cleanup_vst_sensor")
    @patch("vss_agents.api.rtsp_ingest.add_to_rtvi_vlm")
    @patch("vss_agents.api.rtsp_ingest.add_to_vst")
    @patch("vss_agents.api.rtsp_ingest.httpx.AsyncClient")
    async def test_rtvi_vlm_failure_triggers_rollback(
        self, mock_client_class, mock_add_vst, mock_add_rtvi_vlm, mock_cleanup_sensor, mock_cleanup_storage
    ):
        """Test that RTVI-VLM failure triggers VST cleanup in LVS mode.

        Configuring ``rtvi_vlm_base_url`` enables the LVS branch (the router
        derives ``is_lvs_mode`` from a non-empty ``config.rtvi_vlm_url``).
        """
        router = create_rtsp_ingest_router(
            ServiceConfig(
                vst_internal_url="http://vst:30888",
                rtvi_vlm_base_url="http://rtvi-vlm:8018",
            )
        )

        mock_client = MagicMock()
        mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_add_vst.return_value = (True, "OK", "sensor-123", "rtsp://vst:554/sensor-123")
        mock_add_rtvi_vlm.return_value = (False, "RTVI-VLM error", None)
        mock_cleanup_sensor.return_value = (True, "OK")
        mock_cleanup_storage.return_value = (True, "OK")

        endpoint = router.routes[0].endpoint
        request = AddStreamRequest(sensor_url="rtsp://camera:554/stream", name="camera-1")
        response = await endpoint(request)

        assert response.status == "failure"
        assert "RTVI-VLM" in response.message
        mock_cleanup_sensor.assert_called_once()
        mock_cleanup_storage.assert_called_once()

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_ingest.add_to_vst")
    async def test_vst_failure_no_rollback_needed(self, mock_add_vst):
        """Test that VST failure doesn't trigger rollback (nothing to rollback)."""
        router = create_rtsp_ingest_router(
            ServiceConfig(
                vst_internal_url="http://vst:30888",
                rtvi_embed_base_url="http://rtvi-embed:8017",
            )
        )

        mock_add_vst.return_value = (False, "VST returned 500: Server error", None, None)

        endpoint = router.routes[0].endpoint
        request = AddStreamRequest(sensor_url="rtsp://camera:554/stream", name="camera-1")
        response = await endpoint(request)

        assert response.status == "failure"
        assert "VST" in response.message

    @pytest.mark.asyncio
    @patch("vss_agents.api.rtsp_ingest.cleanup_vst_storage")
    @patch("vss_agents.api.rtsp_ingest.cleanup_vst_sensor")
    @patch("vss_agents.api.rtsp_ingest.add_to_rtvi_cv")
    @patch("vss_agents.api.rtsp_ingest.add_to_vst")
    @patch("vss_agents.api.rtsp_ingest.httpx.AsyncClient")
    async def test_rtvi_cv_failure_triggers_rollback(
        self, mock_client_class, mock_add_vst, mock_add_rtvi_cv, mock_cleanup_sensor, mock_cleanup_storage
    ):
        """Test that RTVI-CV failure triggers VST cleanup."""
        router = create_rtsp_ingest_router(
            ServiceConfig(
                vst_internal_url="http://vst:30888",
                rtvi_cv_base_url="http://rtvi-cv:9000",
                rtvi_embed_base_url="http://rtvi-embed:8017",
            )
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


class TestRegisterRtspStreamApiRoutes:
    """Test register_rtsp_ingest_routes function."""

    def test_register_with_full_rtvi_config(self):
        """search-style: VST + RTVI-CV + RTVI-embed all configured, RTVI manages storage."""
        mock_app = MagicMock()
        mock_config = MagicMock()

        mock_streaming_config = MagicMock()
        mock_streaming_config.vst_internal_url = "http://vst:30888"
        mock_streaming_config.rtvi_cv_base_url = "http://rtvi-cv:9000"
        mock_streaming_config.rtvi_embed_base_url = "http://rtvi-embed:8017"
        mock_streaming_config.rtvi_vlm_base_url = "http://rtvi-vlm:8018"
        mock_streaming_config.rtvi_embed_model = "test-model"
        mock_streaming_config.rtvi_embed_chunk_duration = 10
        mock_streaming_config.delete_vst_storage_on_stream_remove = False

        mock_config.general.front_end.streaming_ingest = mock_streaming_config

        register_rtsp_ingest_routes(mock_app, mock_config)

        assert mock_app.include_router.called

    def test_register_vst_only_no_rtvi_urls(self):
        """alerts-style: only VST configured, no RTVI URLs.

        ``register_rtsp_ingest_routes`` no longer requires
        ``rtvi_embed_base_url`` — empty values mean RTVI steps self-skip at
        request time. This must succeed.
        """
        mock_app = MagicMock()
        mock_config = MagicMock()

        mock_streaming_config = MagicMock()
        mock_streaming_config.vst_internal_url = "http://vst:30888"
        mock_streaming_config.rtvi_cv_base_url = ""
        mock_streaming_config.rtvi_embed_base_url = ""
        mock_streaming_config.rtvi_embed_model = "cosmos-embed1-448p"
        mock_streaming_config.rtvi_embed_chunk_duration = 5
        mock_streaming_config.delete_vst_storage_on_stream_remove = True

        mock_config.general.front_end.streaming_ingest = mock_streaming_config

        register_rtsp_ingest_routes(mock_app, mock_config)

        assert mock_app.include_router.called

    def test_register_missing_streaming_ingest_raises(self):
        """Without streaming_ingest configured, registration must fail loudly."""
        mock_app = MagicMock()
        mock_config = MagicMock()
        mock_config.general.front_end = MagicMock(spec=[])  # no streaming_ingest

        with pytest.raises(ValueError, match="streaming_ingest"):
            register_rtsp_ingest_routes(mock_app, mock_config)

    def test_register_missing_vst_url_raises(self):
        """streaming_ingest present but vst_internal_url empty must raise."""
        mock_app = MagicMock()
        mock_config = MagicMock()

        mock_streaming_config = MagicMock()
        mock_streaming_config.vst_internal_url = ""
        mock_streaming_config.rtvi_cv_base_url = ""
        mock_streaming_config.rtvi_embed_base_url = ""
        mock_streaming_config.rtvi_embed_model = "cosmos-embed1-448p"
        mock_streaming_config.rtvi_embed_chunk_duration = 5
        mock_streaming_config.delete_vst_storage_on_stream_remove = True

        mock_config.general.front_end.streaming_ingest = mock_streaming_config

        with pytest.raises(ValueError, match="vst_internal_url"):
            register_rtsp_ingest_routes(mock_app, mock_config)

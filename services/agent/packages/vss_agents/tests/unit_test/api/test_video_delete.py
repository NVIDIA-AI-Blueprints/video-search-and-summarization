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
"""Unit tests for video_delete module.

Covers the RTVI-CV cleanup helper used by ``DELETE /api/v1/videos/{video_id}``.
"""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_agents.api.video_delete import EsCleanupConfig
from vss_agents.api.video_delete import _delete_es_documents
from vss_agents.api.video_delete import _remove_from_rtvi_cv
from vss_agents.api.video_delete import create_video_delete_router
from vss_agents.tools.vst.utils import VSTError


class TestRemoveFromRtviCv:
    """Test _remove_from_rtvi_cv function."""

    @pytest.mark.asyncio
    async def test_successful_remove_sends_stream_routing_header(self):
        """The remove request must carry ``x-stream-id`` so consistent-hash
        routing lands it on the RTVI-CV pod that owns the stream (nvbug 6455296)."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_response)

        success, msg = await _remove_from_rtvi_cv(mock_client, "http://rtvi-cv:9000", "sensor-123", "camera-1")

        assert success is True
        assert msg == "OK"
        mock_client.post.assert_called_once_with(
            "http://rtvi-cv:9000/api/v1/stream/remove",
            json={
                "key": "sensor",
                "value": {
                    "camera_id": "sensor-123",
                    "camera_name": "camera-1",
                    "camera_url": "",
                    "change": "camera_remove",
                    "metadata": {"resolution": "1920x1080", "codec": "h264", "framerate": 30},
                },
                "headers": {"source": "vst"},
            },
            headers={"x-stream-id": "sensor-123"},
        )

    @pytest.mark.asyncio
    async def test_skipped_when_not_configured(self):
        mock_client = MagicMock()

        success, msg = await _remove_from_rtvi_cv(mock_client, "", "sensor-123", "camera-1")

        assert success is True
        assert "Skipped" in msg
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_2xx_reports_failure(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "internal error"
        mock_client.post = AsyncMock(return_value=mock_response)

        success, msg = await _remove_from_rtvi_cv(mock_client, "http://rtvi-cv:9000", "sensor-123", "camera-1")

        assert success is False
        assert "500" in msg

    @pytest.mark.parametrize(
        ("status_code", "body"),
        [
            (404, "Stream not found"),
            (410, "stream is already removed"),
            (400, "Stream not found"),
            (409, "stream already removed"),
            (500, "requested stream does not exist"),
        ],
    )
    @pytest.mark.asyncio
    async def test_already_absent_remove_is_idempotent_success(self, status_code, body):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.text = body
        mock_client.post = AsyncMock(return_value=mock_response)

        success, msg = await _remove_from_rtvi_cv(
            mock_client,
            "http://rtvi-cv:9000",
            "sensor-123",
            "camera-1",
        )

        assert success is True
        assert msg == "already absent"

    @pytest.mark.asyncio
    async def test_non_absence_client_error_reports_failure(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "invalid camera payload"
        mock_client.post = AsyncMock(return_value=mock_response)

        success, msg = await _remove_from_rtvi_cv(
            mock_client,
            "http://rtvi-cv:9000",
            "sensor-123",
            "camera-1",
        )

        assert success is False
        assert "400" in msg

    @pytest.mark.parametrize(
        ("status_code", "body"),
        [
            (404, "Not Found"),
            (410, "route gone"),
            (409, "configuration conflict"),
            (500, "model file not found while removing stream"),
        ],
    )
    @pytest.mark.asyncio
    async def test_non_stream_absence_response_reports_failure(self, status_code, body):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.text = body
        mock_client.post = AsyncMock(return_value=mock_response)

        success, msg = await _remove_from_rtvi_cv(
            mock_client,
            "http://rtvi-cv:9000",
            "sensor-123",
            "camera-1",
        )

        assert success is False
        assert str(status_code) in msg

    @pytest.mark.asyncio
    async def test_network_error_reports_failure(self):
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("boom"))

        success, msg = await _remove_from_rtvi_cv(mock_client, "http://rtvi-cv:9000", "sensor-123", "camera-1")

        assert success is False
        assert "boom" in msg


class TestDeleteEsDocuments:
    """Delete-by-query must fail closed when Elasticsearch reports partial work."""

    @pytest.mark.parametrize(
        "result",
        [
            {"deleted": 1, "timed_out": True},
            {"deleted": 1, "failures": [{"reason": "shard unavailable"}]},
            {"deleted": 1, "version_conflicts": 1},
        ],
    )
    @pytest.mark.asyncio
    async def test_partial_delete_response_reports_failure(self, result):
        client = MagicMock()
        client.delete_by_query = AsyncMock(return_value=result)
        client.close = AsyncMock()

        with patch("vss_agents.api.video_delete.AsyncElasticsearch", return_value=client):
            success, message = await _delete_es_documents(
                "http://elasticsearch:9200",
                "mdx-embed-filtered-*",
                "sensor-123",
                "sensor.id.keyword",
            )

        assert success is False
        assert "incomplete" in message
        client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_complete_delete_response_reports_success(self):
        client = MagicMock()
        client.delete_by_query = AsyncMock(
            return_value={"deleted": 2, "timed_out": False, "failures": [], "version_conflicts": 0}
        )
        client.close = AsyncMock()

        with patch("vss_agents.api.video_delete.AsyncElasticsearch", return_value=client):
            success, message = await _delete_es_documents(
                "http://elasticsearch:9200",
                "mdx-embed-filtered-*",
                "sensor-123",
                "sensor.id.keyword",
            )

        assert success is True
        assert message == "Deleted 2 documents"


class TestDeleteVideoAggregateStatus:
    @pytest.mark.asyncio
    @patch("vss_agents.api.video_delete.verify_vst_cleanup", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.delete_vst_sensor", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.delete_vst_storage", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.get_sensor_id_from_stream_id", new_callable=AsyncMock)
    async def test_final_vst_postcondition_recovers_false_negative(
        self,
        mock_get_sensor_name,
        mock_delete_storage,
        mock_delete_sensor,
        mock_verify_vst_cleanup,
    ):
        mock_get_sensor_name.return_value = "warehouse-ladder"
        mock_delete_storage.return_value = (False, "storage returned 500")
        mock_delete_sensor.return_value = (True, "OK")
        mock_verify_vst_cleanup.return_value = (True, "source and storage absent")
        router = create_video_delete_router(vst_internal_url="http://vst:30888")

        response = await router.routes[0].endpoint("sensor-123")

        assert response.status == "success"
        mock_delete_storage.assert_awaited_once()
        mock_delete_sensor.assert_awaited_once()
        mock_verify_vst_cleanup.assert_awaited_once_with("http://vst:30888", "sensor-123", "warehouse-ladder")

    @pytest.mark.asyncio
    @patch("vss_agents.api.video_delete.verify_vst_cleanup", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.delete_vst_sensor", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.delete_vst_storage", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.get_sensor_id_from_stream_id", new_callable=AsyncMock)
    async def test_final_vst_postcondition_remains_fail_closed(
        self,
        mock_get_sensor_name,
        mock_delete_storage,
        mock_delete_sensor,
        mock_verify_vst_cleanup,
    ):
        mock_get_sensor_name.return_value = "warehouse-ladder"
        mock_delete_storage.return_value = (False, "route not found")
        mock_delete_sensor.return_value = (True, "OK")
        mock_verify_vst_cleanup.return_value = (False, "storage_absent=False")
        router = create_video_delete_router(vst_internal_url="http://vst:30888")

        response = await router.routes[0].endpoint("sensor-123")

        assert response.status == "failure"

    @pytest.mark.asyncio
    @patch("vss_agents.api.video_delete.verify_vst_cleanup", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.delete_vst_sensor", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.delete_vst_storage", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.get_sensor_id_from_stream_id", new_callable=AsyncMock)
    async def test_final_vst_postcondition_checks_successful_mutations(
        self,
        mock_get_sensor_name,
        mock_delete_storage,
        mock_delete_sensor,
        mock_verify_vst_cleanup,
    ):
        mock_get_sensor_name.return_value = "warehouse-ladder"
        mock_delete_storage.return_value = (True, "OK")
        mock_delete_sensor.return_value = (True, "OK")
        mock_verify_vst_cleanup.return_value = (False, "sensor_absent=False")
        router = create_video_delete_router(vst_internal_url="http://vst:30888")

        response = await router.routes[0].endpoint("sensor-123")

        assert response.status == "failure"
        mock_verify_vst_cleanup.assert_awaited_once_with("http://vst:30888", "sensor-123", "warehouse-ladder")

    @pytest.mark.asyncio
    @patch("vss_agents.api.video_delete.verify_vst_cleanup", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.delete_vst_sensor", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.delete_vst_storage", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete._remove_from_rtvi_cv", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete._delete_es_documents", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.get_sensor_id_from_stream_id", new_callable=AsyncMock)
    async def test_optional_rtvi_failure_does_not_downgrade_durable_cleanup(
        self,
        mock_get_sensor_name,
        mock_delete_es,
        mock_remove_rtvi,
        mock_delete_storage,
        mock_delete_sensor,
        mock_verify_vst_cleanup,
    ):
        mock_get_sensor_name.return_value = "warehouse-ladder"
        mock_delete_es.return_value = (True, "Deleted")
        mock_remove_rtvi.return_value = (False, "RTVI-CV returned 500")
        mock_delete_storage.return_value = (True, "OK")
        mock_delete_sensor.return_value = (True, "OK")
        mock_verify_vst_cleanup.return_value = (True, "source and storage absent")
        router = create_video_delete_router(
            vst_internal_url="http://vst:30888",
            rtvi_cv_base_url="http://rtvi-cv:9000",
            es_config=EsCleanupConfig(url="http://elasticsearch:9200"),
        )

        response = await router.routes[0].endpoint("sensor-123")

        assert response.status == "success"
        assert response.warnings == ["RTVI-CV cleanup did not complete: RTVI-CV returned 500"]
        mock_remove_rtvi.assert_awaited_once()
        assert mock_delete_es.await_count == 3

    @pytest.mark.asyncio
    @patch("vss_agents.api.video_delete.verify_vst_cleanup", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.delete_vst_sensor", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.delete_vst_storage", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete._remove_from_rtvi_cv", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete._delete_es_documents", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.get_sensor_id_from_stream_id", new_callable=AsyncMock)
    async def test_already_absent_rtvi_stream_yields_success(
        self,
        mock_get_sensor_name,
        mock_delete_es,
        mock_remove_rtvi,
        mock_delete_storage,
        mock_delete_sensor,
        mock_verify_vst_cleanup,
    ):
        mock_get_sensor_name.return_value = "warehouse-ladder"
        mock_delete_es.return_value = (True, "Deleted")
        mock_remove_rtvi.return_value = (True, "already absent")
        mock_delete_storage.return_value = (True, "OK")
        mock_delete_sensor.return_value = (True, "already absent")
        mock_verify_vst_cleanup.return_value = (True, "source and storage absent")
        router = create_video_delete_router(
            vst_internal_url="http://vst:30888",
            rtvi_cv_base_url="http://rtvi-cv:9000",
            es_config=EsCleanupConfig(url="http://elasticsearch:9200"),
        )

        response = await router.routes[0].endpoint("sensor-123")

        assert response.status == "success"
        assert response.warnings is None
        assert mock_delete_es.await_count == 3

    @pytest.mark.asyncio
    @patch("vss_agents.api.video_delete.verify_vst_cleanup", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.delete_vst_sensor", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.delete_vst_storage", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete._remove_from_rtvi_cv", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete._delete_es_documents", new_callable=AsyncMock)
    @patch("vss_agents.api.video_delete.get_sensor_id_from_stream_id", new_callable=AsyncMock)
    async def test_missing_sensor_name_cannot_report_success(
        self,
        mock_get_sensor_name,
        mock_delete_es,
        mock_remove_rtvi,
        mock_delete_storage,
        mock_delete_sensor,
        mock_verify_vst_cleanup,
    ):
        mock_get_sensor_name.side_effect = VSTError("stream not found")
        mock_delete_es.return_value = (True, "Deleted")
        mock_remove_rtvi.return_value = (True, "already absent")
        mock_delete_storage.return_value = (True, "OK")
        mock_delete_sensor.return_value = (True, "already absent")
        mock_verify_vst_cleanup.return_value = (True, "source and storage absent")
        router = create_video_delete_router(
            vst_internal_url="http://vst:30888",
            rtvi_cv_base_url="http://rtvi-cv:9000",
            es_config=EsCleanupConfig(url="http://elasticsearch:9200"),
        )

        response = await router.routes[0].endpoint("sensor-123")

        assert response.status == "partial"
        mock_delete_es.assert_awaited_once()

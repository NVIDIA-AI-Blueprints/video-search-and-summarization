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

import pytest

from vss_agents.api.video_delete import _remove_from_rtvi_cv


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

    @pytest.mark.asyncio
    async def test_network_error_reports_failure(self):
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("boom"))

        success, msg = await _remove_from_rtvi_cv(mock_client, "http://rtvi-cv:9000", "sensor-123", "camera-1")

        assert success is False
        assert "boom" in msg

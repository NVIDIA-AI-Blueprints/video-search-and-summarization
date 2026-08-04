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

"""Shared fixtures for realtime API unit tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from realtime.schemas import AlertRuleConfig
from realtime.services.realtime_service import RealtimeAlertService
from realtime.services.incident_service import IncidentService


# ---------------------------------------------------------------------------
# AlertRuleConfig helpers
# ---------------------------------------------------------------------------

SAMPLE_RTSP_URL = "rtsp://localhost:554/stream1"


def make_config(**overrides) -> AlertRuleConfig:
    """Build an AlertRuleConfig with sensible test defaults."""
    defaults = dict(
        live_stream_url=SAMPLE_RTSP_URL,
        alert_type="collision",
        prompt="Detect collisions",
        sensor_id="test-sensor-001",
        model="test-model",
    )
    defaults.update(overrides)
    return AlertRuleConfig(**defaults)


# ---------------------------------------------------------------------------
# RTVI client mock
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_rtvi_client():
    """Return an AsyncMock that simulates RTVIVLMClient.

    ``get_stream_info`` defaults to ``[]`` so :meth:`start_alert` always
    falls through to ``streams/add`` in the happy path. Tests that exercise
    the reuse branch override this fixture's return value to include the
    expected stream entry.
    """
    client = AsyncMock()
    client.start_stream.return_value = {
        "results": [{"id": "stream-abc-123", "status": "added"}]
    }
    client.get_stream_info.return_value = []
    client.generate_captions.return_value = {
        "status": "started", "stream_id": "stream-abc-123"
    }
    client.stop_captions.return_value = {"status": "stopped"}
    client.stop_stream.return_value = {"status": "deleted"}
    client.aclose.return_value = None
    return client


# ---------------------------------------------------------------------------
# RealtimeAlertService with mock RTVI
# ---------------------------------------------------------------------------

@pytest.fixture()
def realtime_service(mock_rtvi_client):
    """Create a RealtimeAlertService with mocked config and RTVI client."""
    with patch("realtime.services.realtime_service.load_config", return_value={
        "rtvi_vlm": {
            "base_url": "http://mock:8000",
            "timeout": 5,
            "default_model": "default-vlm",
            "captions_ack_timeout": 0.1,
            "stream_readiness_poll_interval": 0.01,
            "stream_readiness_max_wait": 0.05,
        }
    }):
        svc = RealtimeAlertService()
    svc._client = mock_rtvi_client
    return svc


# ---------------------------------------------------------------------------
# IncidentService with mock ES client
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_es_client():
    """Return a MagicMock simulating ElasticClient."""
    es = MagicMock()
    es.client.search.return_value = {
        "hits": {
            "total": {"value": 2, "relation": "eq"},
            "hits": [
                {
                    "_id": "doc-1",
                    "_index": "mdx-vlm-incidents-2025-01-01",
                    "_source": {"sensorId": "cam-1", "category": "collision", "timestamp": "2025-01-01T00:00:00Z"},
                },
                {
                    "_id": "doc-2",
                    "_index": "mdx-vlm-incidents-2025-01-01",
                    "_source": {"sensorId": "cam-2", "category": "fire", "timestamp": "2025-01-01T01:00:00Z"},
                },
            ],
        }
    }
    return es


@pytest.fixture()
def incident_service(mock_es_client):
    """Create an IncidentService with injected mock ES client."""
    return IncidentService(es_client=mock_es_client, index_base="mdx-vlm-incidents")

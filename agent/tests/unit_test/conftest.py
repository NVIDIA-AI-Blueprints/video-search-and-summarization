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
"""Common pytest fixtures for unit tests."""

from datetime import UTC
from datetime import datetime
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_llm():
    """Create a mock LLM object for testing."""
    llm = MagicMock()
    llm.model_name = "test-model"
    return llm


@pytest.fixture
def mock_llm_response():
    """Create a mock LLM response object."""
    response = MagicMock()
    response.content = "Test content"
    response.reasoning_content = None
    response.additional_kwargs = {}
    response.response_metadata = {}
    return response


@pytest.fixture
def sample_video_event():
    """Create a sample video event for testing."""
    return {"start_timestamp": 10.5, "end_timestamp": 25.0, "event_description": "A person walking across the street"}


@pytest.fixture
def sample_incidents():
    """Create sample incident data for testing."""
    return [
        {
            "Id": "incident-001",
            "sensorId": "sensor-001",
            "timestamp": "2025-01-15T10:00:00.000Z",
            "end": "2025-01-15T10:05:00.000Z",
            "category": "traffic_violation",
            "type": "mdx-incidents",
            "isAnomaly": False,
        },
        {
            "Id": "incident-002",
            "sensorId": "sensor-001",
            "timestamp": "2025-01-15T10:10:00.000Z",
            "end": "2025-01-15T10:15:00.000Z",
            "category": "jaywalking",
            "type": "mdx-incidents",
            "isAnomaly": True,
        },
    ]


@pytest.fixture
def sample_sensors():
    """Create sample sensor data for testing."""
    return [
        {
            "id": "sensor-001",
            "place": [
                {"value": "San Jose"},
                {"value": "Intersection_A"},
            ],
        },
        {
            "id": "sensor-002",
            "place": [
                {"value": "San Jose"},
                {"value": "Intersection_B"},
            ],
        },
        {
            "id": "sensor-003",
            "place": [
                {"value": "Mountain View"},
                {"value": "Intersection_C"},
            ],
        },
    ]


@pytest.fixture
def sample_markdown_report():
    """Create a sample markdown report for testing."""
    return """# Test Report

## Summary
| Field | Value |
|-------|-------|
| Location | San Jose |
| Time | 10:00 AM |

## Details
### Incident Information
| Field | Value |
|-------|-------|
| Type | Traffic Violation |
| Duration | 5 minutes |

**Incident Snapshot:** [View](http://example.com/snapshot.jpg)
**Incident Video:** [View](http://example.com/video.mp4)
"""


@pytest.fixture
def sample_geocoding_response():
    """Create a sample geocoding response for testing."""
    return {
        "features": [
            {
                "properties": {
                    "geocoding": {
                        "type": "street",
                        "city": "San Jose",
                        "county": "Santa Clara County",
                        "state": "California",
                        "country": "United States",
                        "name": "Main Street",
                        "label": "123 Main Street, San Jose, CA",
                        "osm_key": "highway",
                        "osm_value": "residential",
                        "extra": {"maxspeed": "35"},
                    }
                }
            }
        ]
    }


@pytest.fixture
def mock_async_http_response():
    """Create a mock async HTTP response."""
    response = AsyncMock()
    response.status = 200
    return response


@pytest.fixture
def utc_now():
    """Get current UTC time."""
    return datetime.now(UTC)

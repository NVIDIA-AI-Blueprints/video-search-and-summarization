from unittest.mock import AsyncMock, MagicMock

import pytest

SAMPLE_RECORD = {
    "objStorePath": "deft-aoi/3f8a1b2c/photo1.jpg",
    "status": "completed",
    "cropCount": 1,
    "completedCount": 1,
    "failedCount": 0,
    "deployment_type": "Shadow",
    "cropResults": [
        {
            "cropId": "U16",
            "status": "completed",
            "score": 0.999967,
            "verdict": "DEFECT",
            "threshold": 0.332784,
            "old_model": {"version": "1", "score": 0.999967, "verdict": "DEFECT"},
            "new_model": {"version": "2", "score": 0.8, "verdict": "DEFECT"},
        }
    ],
}


@pytest.fixture
def mock_forwarder():
    m = MagicMock()
    m.forward = AsyncMock(return_value=True)
    m.close = AsyncMock()
    return m


@pytest.fixture
def mock_consumer():
    m = MagicMock()
    m.start = AsyncMock()
    m.stop = AsyncMock()
    m.is_ready = True
    return m

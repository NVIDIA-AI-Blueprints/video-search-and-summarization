from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_inference():
    m = MagicMock()
    m.ready = AsyncMock(return_value=True)
    m.close = AsyncMock()
    m.infer_batch = AsyncMock()
    return m


@pytest.fixture
def mock_kafka():
    m = MagicMock()
    m.start = AsyncMock()
    m.stop = AsyncMock()
    m.publish = AsyncMock()
    m.is_ready = True
    return m


@pytest.fixture
def client(mock_inference, mock_kafka):
    from client.api.router import get_inference, get_kafka
    from client.application import app

    app.dependency_overrides[get_inference] = lambda: mock_inference
    app.dependency_overrides[get_kafka] = lambda: mock_kafka
    with (
        patch("client.application.KafkaPublisher", return_value=mock_kafka),
        patch("client.application.InferenceClient", return_value=mock_inference),
    ):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()

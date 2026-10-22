import base64
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from backend.application import app
from backend.api.router import get_storage

VALID_B64 = base64.b64encode(b"fake-image-bytes").decode()

VALID_METADATA = {
    "version": "1.0",
    "description": "test image",
    "timestamp": "2026-06-12T00:00:00Z",
    "machineInfo": {"machineId": "machine-1", "location": "line-1", "jobType": "inspection"},
    "aoiDefects": {"count": 0, "details": []},
    # imageSize here is a stand-in client value; the backend overwrites it with
    # the real decoded byte length (see test_image_size_is_set_to_decoded_byte_length).
    "imageInfo": {"imageType": "RGB", "imageSize": 999999},
}

VALID_METADATA_WITH_CROPS = {
    **VALID_METADATA,
    "imageInfo": {
        "cropInfo": [
            {
                "id": "U7",
                "label": "IC",
                "referenceLabels": None,
                "crop": {
                    "cropType": "bbox",
                    "coordinates": {"left": 10, "top": 20, "width": 64, "height": 64},
                },
                "aoiDefectInfo": {"defectType": "bridge"},
            }
        ],
    },
}

VALID_INFERENCE_RESULT = {
    "objStorePath": "deft-aoi/abc123/board.png",
    "status": "completed",
    "cropCount": 1,
    "completedCount": 1,
    "failedCount": 0,
    "deployment_type": "normal",
    "model_name": "changenet_classify",
    "cropResults": [
        {
            "cropId": "U7",
            "status": "completed",
            "score": 0.36,
            "verdict": "PASS",
            "threshold": 0.5,
            "old_model": {"version": "v1", "score": 0.36, "verdict": "PASS"},
            "new_model": {"version": "v2", "score": 0.36, "verdict": "PASS"},
        }
    ],
}


def make_image(filename: str = "photo.jpg", data: str = VALID_B64, metadata: dict | None = None) -> dict:
    return {
        "filename": filename,
        "content_type": "image/jpeg",
        "data": data,
        "metadata": metadata if metadata is not None else VALID_METADATA,
    }


async def drain_aoi_client(svc) -> None:
    """Wait for background tasks started by schedule_trigger (test helper)."""
    tasks = list(svc._tasks)
    if tasks:
        await asyncio.gather(*tasks)


@pytest.fixture
def mock_storage():
    storage = MagicMock()
    storage.upload = AsyncMock(return_value="http://localhost:8333/test-bucket/samples/uuid/photo.jpg")
    storage.delete = AsyncMock()
    storage.put_bytes = AsyncMock()
    storage.get_bytes = AsyncMock()
    storage.exists = AsyncMock(return_value=False)
    storage.get_metadata = AsyncMock(
        return_value={
            **VALID_METADATA_WITH_CROPS,
            "objStorePath": VALID_INFERENCE_RESULT["objStorePath"],
        }
    )
    return storage


@pytest.fixture
def mock_kafka():
    kafka = MagicMock()
    kafka.send = AsyncMock()
    kafka.start = AsyncMock()
    kafka.stop = AsyncMock()
    return kafka


@pytest.fixture
def mock_aoi_client():
    client = MagicMock()
    client.schedule_trigger = MagicMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_elasticsearch():
    es = MagicMock()
    es.upsert_inference_result = AsyncMock(return_value=True)
    es.find_document_by_obj_store_path = AsyncMock(return_value={"_index": "aoi_machine_1", "_id": "doc1"})
    es.close = AsyncMock()
    return es


@pytest.fixture
def client(mock_storage, mock_kafka, mock_aoi_client, mock_elasticsearch):
    app.dependency_overrides[get_storage] = lambda: mock_storage
    with patch("backend.application.KafkaService", return_value=mock_kafka), \
         patch("backend.application.AoiClientService", return_value=mock_aoi_client), \
         patch("backend.application.ElasticsearchService", return_value=mock_elasticsearch):
        with TestClient(app) as c:
            c.app.state.aoi_client = mock_aoi_client
            c.app.state.elasticsearch = mock_elasticsearch
            yield c
    app.dependency_overrides.clear()

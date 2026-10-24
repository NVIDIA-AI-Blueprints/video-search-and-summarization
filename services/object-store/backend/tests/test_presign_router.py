from contextlib import contextmanager

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.api.presign_router import get_storage
from backend.api.presign_service import PresignError, resolve_object_key
from backend.application import app
from backend.config import settings
from fastapi.testclient import TestClient


@contextmanager
def _patched_lifespan():
    """Stub the lifespan services so TestClient doesn't start a real Kafka/AOI/ES."""
    kafka = MagicMock(start=AsyncMock(), stop=AsyncMock())
    aoi = MagicMock(close=AsyncMock())
    es = MagicMock(close=AsyncMock())
    with patch("backend.application.KafkaService", return_value=kafka), \
         patch("backend.application.AoiClientService", return_value=aoi), \
         patch("backend.application.ElasticsearchService", return_value=es):
        yield


def test_resolve_object_key_from_obj_store_path():
    assert resolve_object_key("deft-aoi/abc/board.png") == "deft-aoi/abc/board.png"


def test_resolve_object_key_from_full_url():
    url = f"{settings.aws_endpoint}/{settings.aws_bucket}/deft-aoi/abc/board.png"
    assert resolve_object_key(url) == "deft-aoi/abc/board.png"


def test_resolve_object_key_rejects_traversal():
    with pytest.raises(PresignError):
        resolve_object_key("deft-aoi/../etc/passwd")


@pytest.fixture
def presign_client():
    storage = MagicMock()
    storage.exists = AsyncMock(return_value=True)
    storage.generate_presigned_get_url = AsyncMock(
        return_value="http://signed.example/obj?X-Amz-Signature=abc"
    )
    app.dependency_overrides[get_storage] = lambda: storage
    with _patched_lifespan(), TestClient(app) as client:
        app.state.storage = storage
        yield client, storage
    app.dependency_overrides.clear()


def test_get_presigned_urls_success(presign_client):
    client, storage = presign_client
    resp = client.post(
        "/v1/get_presigned_urls",
        json={"urls": ["deft-aoi/abc/board.png"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["key"] == "deft-aoi/abc/board.png"
    assert "X-Amz-Signature" in body["items"][0]["presigned_url"]
    assert body["failed"] == []
    storage.exists.assert_awaited_once_with("deft-aoi/abc/board.png")
    storage.generate_presigned_get_url.assert_awaited_once()


def test_get_presigned_urls_missing_object(presign_client):
    client, storage = presign_client
    storage.exists = AsyncMock(return_value=False)
    resp = client.post(
        "/v1/get_presigned_urls",
        json={"urls": ["deft-aoi/missing/board.png"]},
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []
    assert resp.json()["failed"][0]["error"].startswith("object not found")


def test_get_presigned_urls_rejects_empty_list(presign_client):
    client, _ = presign_client
    resp = client.post("/v1/get_presigned_urls", json={"urls": []})
    assert resp.status_code == 422


def test_get_presigned_urls_rejects_over_batch_limit(presign_client):
    client, _ = presign_client
    urls = [f"deft-aoi/x/{i}.png" for i in range(settings.max_presign_urls_per_request + 1)]
    resp = client.post("/v1/get_presigned_urls", json={"urls": urls})
    assert resp.status_code == 422


# ---- presigned POST (upload) -------------------------------------------------


@pytest.fixture
def presign_post_client():
    storage = MagicMock()
    storage.exists = AsyncMock(return_value=False)
    storage.generate_presigned_post = AsyncMock(
        return_value={
            "url": "http://signed.example/deft-aoi",
            "fields": {"key": "boards/b1", "Content-Type": "image/png"},
        }
    )
    app.dependency_overrides[get_storage] = lambda: storage
    with _patched_lifespan(), TestClient(app) as client:
        app.state.storage = storage
        yield client, storage
    app.dependency_overrides.clear()


def _post_body(folder="boards", file_name="b1", content_type="image/png"):
    return {"content_type": content_type, "folder": folder, "file_name": file_name}


def test_get_presigned_post_url_success(presign_post_client):
    client, storage = presign_post_client
    resp = client.post("/v1/get_presigned_post_url", json=_post_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"] == "http://signed.example/deft-aoi"
    assert body["fields"]["key"] == "boards/b1"  # returned verbatim from storage
    storage.exists.assert_awaited_once_with(f"{settings.upload_folder}/boards/b1")
    storage.generate_presigned_post.assert_awaited_once()


def test_get_presigned_post_url_conflict_returns_409(presign_post_client):
    client, storage = presign_post_client
    storage.exists = AsyncMock(return_value=True)
    resp = client.post("/v1/get_presigned_post_url", json=_post_body())
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


def test_get_presigned_post_url_rejects_nested_folder(presign_post_client):
    client, _ = presign_post_client
    resp = client.post("/v1/get_presigned_post_url", json=_post_body(folder="a/b"))
    assert resp.status_code == 422


def test_get_presigned_post_url_rejects_bad_file_name(presign_post_client):
    client, _ = presign_post_client
    resp = client.post("/v1/get_presigned_post_url", json=_post_body(file_name="../x"))
    assert resp.status_code == 422

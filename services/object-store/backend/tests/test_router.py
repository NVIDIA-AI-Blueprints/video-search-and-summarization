import pytest
from unittest.mock import AsyncMock

from backend.application import app
from tests.conftest import VALID_INFERENCE_RESULT, make_image


class TestUploadSamples:
    def test_single_image_success(self, client):
        resp = client.post("/api/samples", json={"images": [make_image()]})
        assert resp.status_code == 207
        body = resp.json()
        assert len(body["uploaded"]) == 1
        assert body["failed"] == []

    def test_multiple_images_all_succeed(self, client):
        images = [make_image(f"img{i}.jpg") for i in range(3)]
        resp = client.post("/api/samples", json={"images": images})
        assert resp.status_code == 207
        body = resp.json()
        assert len(body["uploaded"]) == 3
        assert body["failed"] == []

    def test_partial_failure_returns_207(self, client, mock_storage):
        mock_storage.upload = AsyncMock(
            side_effect=[
                "http://localhost:8333/test-bucket/samples/uuid/photo.jpg",
                Exception("disk full"),
            ]
        )
        resp = client.post(
            "/api/samples",
            json={"images": [make_image("ok.jpg"), make_image("bad.jpg")]},
        )
        assert resp.status_code == 207
        body = resp.json()
        assert len(body["uploaded"]) == 1
        assert len(body["failed"]) == 1


class TestValidation:
    def test_empty_images_returns_422(self, client):
        resp = client.post("/api/samples", json={"images": []})
        assert resp.status_code == 422


class TestInferenceResults:
    def test_accept_valid_record(self, client, mock_kafka):
        resp = client.post("/api/inference-results", json=VALID_INFERENCE_RESULT)
        assert resp.status_code == 200
        body = resp.json()
        assert body["objStorePath"] == VALID_INFERENCE_RESULT["objStorePath"]
        assert body["updated"] is True
        # inference result is republished to Kafka (same ingest path as upload)
        mock_kafka.send.assert_awaited_once()
        sent = mock_kafka.send.call_args.args[0]
        assert sent["objStorePath"] == VALID_INFERENCE_RESULT["objStorePath"]
        assert sent["inferenceSummary"]["status"] == VALID_INFERENCE_RESULT["status"]

    def test_missing_object_metadata_returns_503(self, client, mock_storage):
        mock_storage.get_metadata = AsyncMock(side_effect=Exception("not found"))
        resp = client.post("/api/inference-results", json=VALID_INFERENCE_RESULT)
        assert resp.status_code == 503

    def test_invalid_schema_returns_422(self, client):
        bad = {**VALID_INFERENCE_RESULT, "status": 123}
        resp = client.post("/api/inference-results", json=bad)
        assert resp.status_code == 422

    def test_kafka_error_returns_503(self, client, mock_kafka):
        mock_kafka.send = AsyncMock(side_effect=Exception("broker down"))
        resp = client.post("/api/inference-results", json=VALID_INFERENCE_RESULT)
        assert resp.status_code == 503

    def test_idempotent_replay_succeeds(self, client, mock_kafka):
        for _ in range(2):
            resp = client.post("/api/inference-results", json=VALID_INFERENCE_RESULT)
            assert resp.status_code == 200
        assert mock_kafka.send.await_count == 2

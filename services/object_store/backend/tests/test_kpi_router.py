import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.api.kpi_router import get_es
from backend.application import app


@pytest.fixture
def override_es():
    es = MagicMock()
    es.open_pit = AsyncMock(return_value="PIT-xyz")
    # create_kpi pre-checks the selection is non-empty before opening the PIT;
    # a non-zero default keeps that gate out of the way of the routing tests.
    es.catalog_totals = AsyncMock(return_value=(42, 4200))
    app.dependency_overrides[get_es] = lambda: es
    yield es
    app.dependency_overrides.pop(get_es, None)


class TestCreateKpi:
    def test_returns_202_and_kpi_id(self, client, override_es):
        resp = client.post("/v1/kpi", json={"selection": {"tag": "kpi"}})
        assert resp.status_code == 202
        assert resp.json()["kpi_id"]

    def test_missing_selection_returns_422(self, client, override_es):
        resp = client.post("/v1/kpi", json={})
        assert resp.status_code == 422

    def test_missing_tag_returns_422(self, client, override_es):
        resp = client.post("/v1/kpi", json={"selection": {}})
        assert resp.status_code == 422

    def test_fanout_failure_returns_503(self, client, override_es):
        override_es.close_pit = AsyncMock()
        app.state.kafka.send = AsyncMock(side_effect=RuntimeError("broker down"))
        resp = client.post("/v1/kpi", json={"selection": {"tag": "kpi"}})
        assert resp.status_code == 503


class TestGetKpi:
    def test_returns_finalized(self, client, mock_storage):
        doc = {
            "kpi_id": "k1",
            "status": "ready",
            "total_records": 5,
            "manifests": [
                {"format": "jsonl", "uri": "s3://b/k", "size_bytes": 10, "sha256": "x", "record_count": 5}
            ],
        }
        mock_storage.exists = AsyncMock(return_value=True)
        mock_storage.get_bytes = AsyncMock(return_value=json.dumps(doc).encode())
        resp = client.get("/v1/kpi/k1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["total_records"] == 5
        assert body["manifests"][0]["uri"] == "s3://b/k"

    def test_returns_creating(self, client, mock_storage):
        async def _exists(key):
            return key.endswith("_request.json")

        mock_storage.exists = AsyncMock(side_effect=_exists)
        mock_storage.get_bytes = AsyncMock(
            return_value=json.dumps({"kpi_id": "k2", "requested_at": "2026-07-23T00:00:00Z"}).encode()
        )
        resp = client.get("/v1/kpi/k2")
        assert resp.status_code == 200
        assert resp.json()["status"] == "creating"

    def test_returns_404_when_unknown(self, client, mock_storage):
        mock_storage.exists = AsyncMock(return_value=False)
        resp = client.get("/v1/kpi/nope")
        assert resp.status_code == 404

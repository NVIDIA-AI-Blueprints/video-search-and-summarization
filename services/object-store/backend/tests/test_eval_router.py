import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from backend.api import eval_router
from backend.application import app
from backend.shared import eval_layout


class InMemStore:
    def __init__(self):
        self.objs: dict[str, bytes] = {}

    async def put_object(self, key, data, content_type="application/json"):
        self.objs[key] = data
        return f"url/{key}"

    async def get_object(self, key):
        return self.objs.get(key)


@pytest.fixture
def eval_client():
    store = InMemStore()
    es = MagicMock(open_pit=AsyncMock(return_value="pit-1"), close_pit=AsyncMock())
    kafka = MagicMock(send=AsyncMock())

    mock_kafka = MagicMock(start=AsyncMock(), stop=AsyncMock(), send=AsyncMock())
    mock_aoi = MagicMock(schedule_trigger=MagicMock(), close=AsyncMock())
    mock_es_state = MagicMock(close=AsyncMock())

    app.dependency_overrides[eval_router.get_storage] = lambda: store
    app.dependency_overrides[eval_router.get_es] = lambda: es
    app.dependency_overrides[eval_router.get_kafka] = lambda: kafka
    with patch("backend.application.KafkaService", return_value=mock_kafka), \
         patch("backend.application.AoiClientService", return_value=mock_aoi), \
         patch("backend.application.ElasticsearchService", return_value=mock_es_state):
        with TestClient(app) as c:
            c.store = store
            c.es = es
            c.kafka = kafka
            yield c
    app.dependency_overrides.clear()


VALID = {"selection": {"model": "m1", "version": "v1", "role": "candidate"}}


def test_post_results_201_returns_only_eval_id(eval_client):
    resp = eval_client.post("/v1/results", json=VALID)
    assert resp.status_code == 201
    body = resp.json()
    assert body["eval_id"]
    assert set(body.keys()) == {"eval_id"}  # contract CreateEvaluationSetResponse
    assert eval_client.kafka.send.await_count == 3


def test_post_results_missing_role_422(eval_client):
    resp = eval_client.post(
        "/v1/results", json={"selection": {"model": "m", "version": "v"}}
    )
    assert resp.status_code == 422


def test_post_results_rejects_eval_id_422(eval_client):
    resp = eval_client.post("/v1/results", json={**VALID, "eval_id": "x"})
    assert resp.status_code == 422  # additionalProperties: false


def test_post_results_fanout_failure_503(eval_client):
    async def flaky(*a, **k):
        if k["partition"] == 0:
            raise RuntimeError("kafka down")

    eval_client.kafka.send = AsyncMock(side_effect=flaky)
    resp = eval_client.post("/v1/results", json=VALID)
    assert resp.status_code == 503
    eval_client.es.close_pit.assert_awaited_once()


def test_get_eval_404_unknown(eval_client):
    assert eval_client.get("/v1/eval/nope").status_code == 404


def test_get_eval_creating(eval_client):
    eval_client.store.objs[eval_layout.status_key("e1")] = json.dumps(
        {"eval_id": "e1", "status": "creating", "created_at": "t0"}
    ).encode()
    r = eval_client.get("/v1/eval/e1")
    assert r.status_code == 200
    b = r.json()
    assert set(b.keys()) == {"eval_id", "status", "total_records", "manifests", "created_at"}
    assert b["status"] == "creating"
    assert b["total_records"] == 0 and b["manifests"] == []
    assert b["created_at"] == "t0"


def test_get_eval_ready_with_manifests(eval_client):
    eval_client.store.objs[eval_layout.status_key("e1")] = json.dumps(
        {"eval_id": "e1", "status": "ready", "created_at": "t0"}
    ).encode()
    eval_client.store.objs[eval_layout.summary_key("e1")] = json.dumps({
        "total_records": 100,
        "manifests": [{
            "format": "jsonl", "uri": "s3://b/evals/e1/manifest0.jsonl",
            "size_bytes": 10, "sha256": "ab",
        }],
    }).encode()
    r = eval_client.get("/v1/eval/e1")
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ready"
    assert b["total_records"] == 100
    assert b["manifests"][0]["format"] == "jsonl"
    assert b["manifests"][0]["uri"].startswith("s3://")

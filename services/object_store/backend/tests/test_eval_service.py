import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.api.eval_schemas import EvalRequest
from backend.api.eval_service import (
    EvalFanoutError,
    compile_eval_selection,
    create_eval,
    get_eval_status,
)
from backend.config import settings
from backend.shared import eval_layout


class InMemStore:
    def __init__(self):
        self.objs: dict[str, bytes] = {}

    async def put_object(self, key, data, content_type="application/json"):
        self.objs[key] = data
        return f"url/{key}"

    async def get_object(self, key):
        return self.objs.get(key)


def make_request(**selection):
    sel = {"model": "m1", "version": "v1", "role": "candidate", **selection}
    return EvalRequest(selection=sel)


@pytest.fixture
def mock_es():
    es = MagicMock()
    es.open_pit = AsyncMock(return_value="pit-1")
    es.close_pit = AsyncMock()
    return es


@pytest.fixture
def mock_kafka():
    kafka = MagicMock()
    kafka.send = AsyncMock()
    return kafka


# ---------- compile_eval_selection ----------

def test_compile_selection_terms_and_range():
    q = compile_eval_selection({
        "model": "m1", "version": "v2", "role": "golden",
        "from": "2026-06-01T00:00:00Z", "to": "2026-07-01T00:00:00Z",
    })
    filters = q["bool"]["filter"]

    # model/version -> ONE nested query on cropInfo; inside it, old_model OR
    # new_model, each an AND of model_name + version on the same crop element.
    nested = [f for f in filters if "nested" in f][0]["nested"]
    assert nested["path"] == "metadata.content_metadata.cropInfo"
    inner = nested["query"]["bool"]
    assert inner["minimum_should_match"] == 1
    matched = {}
    for slot in inner["should"]:
        for term in slot["bool"]["filter"]:
            matched.update(term["term"])
    cp = "metadata.content_metadata.cropInfo"
    assert matched[f"{cp}.inference.old_model.model_name"] == "m1"
    assert matched[f"{cp}.inference.old_model.version"] == "v2"
    assert matched[f"{cp}.inference.new_model.model_name"] == "m1"
    assert matched[f"{cp}.inference.new_model.version"] == "v2"

    # role -> deployment_type term filter.
    terms = {k: v for f in filters if "term" in f for k, v in f["term"].items()}
    assert terms["metadata.content_metadata.inferenceSummary.deployment_type"] == "golden"

    rng = [f["range"] for f in filters if "range" in f][0]
    assert rng["metadata.content_metadata.timestamp"] == {
        "gte": "2026-06-01T00:00:00Z", "lt": "2026-07-01T00:00:00Z",
    }


def test_compile_selection_omits_range_without_bounds():
    q = compile_eval_selection({"model": "m", "version": "v", "role": "r"})
    assert not any("range" in f for f in q["bool"]["filter"])


# ---------- create_eval ----------

async def test_create_eval_generates_id_and_fans_out(mock_es, mock_kafka):
    store = InMemStore()
    eval_id = await create_eval(mock_es, mock_kafka, store, make_request())
    assert eval_id  # server-generated (no caller id)
    assert mock_kafka.send.await_count == settings.eval_partitions
    parts = {c.kwargs["partition"] for c in mock_kafka.send.await_args_list}
    assert parts == set(range(settings.eval_partitions))
    status = json.loads(store.objs[eval_layout.status_key(eval_id)])
    assert status["status"] == "creating"
    work = mock_kafka.send.await_args_list[0].args[0]
    assert work["pit_id"] == "pit-1"
    assert work["query"]["bool"]["filter"]  # compiled query travels in the work item


async def test_fanout_failure_closes_pit_and_marks_failed(mock_es):
    store = InMemStore()
    kafka = MagicMock()

    async def flaky(*a, **k):
        if k["partition"] == 1:
            raise RuntimeError("kafka down")

    kafka.send = AsyncMock(side_effect=flaky)
    with pytest.raises(EvalFanoutError):
        await create_eval(mock_es, kafka, store, make_request())
    mock_es.close_pit.assert_awaited_once_with("pit-1")
    key = next(k for k in store.objs if k.endswith("status.json"))
    assert json.loads(store.objs[key])["status"] == "failed"


async def test_create_eval_closes_pit_if_status_write_fails(mock_es):
    # PIT must not leak if anything after open_pit() raises (e.g. the status PUT).
    store = MagicMock()
    store.put_object = AsyncMock(side_effect=RuntimeError("s3 down"))
    kafka = MagicMock(send=AsyncMock())
    with pytest.raises(RuntimeError):
        await create_eval(mock_es, kafka, store, make_request())
    mock_es.close_pit.assert_awaited_once_with("pit-1")


# ---------- get_eval_status (contract EvaluationSetStats) ----------

async def test_get_eval_status_none_for_unknown():
    assert await get_eval_status(InMemStore(), "nope") is None


async def test_get_eval_status_creating_before_finalize():
    store = InMemStore()
    store.objs[eval_layout.status_key("e1")] = json.dumps(
        {"eval_id": "e1", "status": "creating", "created_at": "t0"}
    ).encode()
    r = await get_eval_status(store, "e1")
    assert r == {
        "eval_id": "e1", "status": "creating", "total_records": 0,
        "manifests": [], "created_at": "t0",
    }


async def test_get_eval_status_ready_with_manifests():
    store = InMemStore()
    store.objs[eval_layout.status_key("e1")] = json.dumps(
        {"eval_id": "e1", "status": "ready", "created_at": "t0"}
    ).encode()
    store.objs[eval_layout.summary_key("e1")] = json.dumps({
        "total_records": 42,
        "manifests": [{
            "format": "jsonl", "uri": "s3://b/evals/e1/manifest0.jsonl",
            "size_bytes": 10, "sha256": "ab",
        }],
    }).encode()
    r = await get_eval_status(store, "e1")
    assert r["status"] == "ready"
    assert r["total_records"] == 42
    assert r["manifests"][0]["uri"].startswith("s3://")


async def test_get_eval_status_failed_maps():
    store = InMemStore()
    store.objs[eval_layout.status_key("e1")] = json.dumps(
        {"eval_id": "e1", "status": "failed", "created_at": "t0"}
    ).encode()
    r = await get_eval_status(store, "e1")
    assert r["status"] == "failed"

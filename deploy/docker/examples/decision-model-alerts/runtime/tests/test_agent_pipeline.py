# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import httpx
import pytest
from fastapi.testclient import TestClient
from agent_pipeline.contracts import Rule, Question
from agent_pipeline.store import Store
from agent_pipeline.rules import process_decision
from agent_pipeline.ingest import create_app
from agent_pipeline.review import review_event
from agent_pipeline.indexer import index_document


@pytest.fixture(autouse=True)
def isolated_clip_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("D1_CLIP_CACHE", str(tmp_path / "clips"))


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state.sqlite")


def rule(**overrides):
    return Rule(stream_id="beach", config_revision=1, field="probability", probability_key="0",
                value=0.8, **overrides)


def decision(index, probability=0.9, revision=1, at=None):
    return {"schema_version": 1, "kind": "d1.decision", "id": str(index), "stream_id": "beach",
            "captured_at": at if at is not None else 1000 + index * 2, "config_revision": revision,
            "answer": {"score": 0.3, "probabilities": {"0": probability}}, "vios_sensor": "beach",
            "question": {"type": "score", "instructions": "How busy is the beach?",
                         "criteria": ["Very busy", "Busy", "Not busy", "Empty"]}}


def test_threshold_consecutive_cooldown_and_duplicates(store):
    store.put_config("rule", "busy", rule().model_dump())
    assert process_decision(store, decision(1)) == []
    assert process_decision(store, decision(1)) == []
    assert process_decision(store, decision(2)) == []
    events = process_decision(store, decision(3))
    assert len(events) == 1
    assert events[0]["decision_ids"] == ["1", "2", "3"]
    assert events[0]["clip_start"] == 996
    assert events[0]["clip_end"] == 1011
    for i in range(4, 10):
        assert process_decision(store, decision(i)) == []
    assert len(store.rows("SELECT * FROM jobs")) == 1
    assert len(store.rows("SELECT * FROM outbox")) == 1


def test_rule_state_survives_restart(store):
    store.put_config("rule", "busy", rule().model_dump())
    process_decision(store, decision(1))
    process_decision(store, decision(2))
    path = store.rows("PRAGMA database_list")[0]["file"]
    restarted = Store(path)
    assert len(process_decision(restarted, decision(3))) == 1
    assert process_decision(restarted, decision(3)) == []


def test_transaction_rolls_back_before_kafka_commit(store, monkeypatch):
    store.put_config("rule", "busy", rule(consecutive_samples=1).model_dump())
    original = store.enqueue

    def fail(*args):
        raise OSError("simulated persistence failure")

    monkeypatch.setattr(store, "enqueue", fail)
    with pytest.raises(OSError):
        process_decision(store, decision(1))
    assert store.rows("SELECT * FROM consumed") == []
    assert store.rows("SELECT * FROM jobs") == []
    monkeypatch.setattr(store, "enqueue", original)
    assert len(process_decision(store, decision(1))) == 1


def test_editing_a_rule_clears_old_consecutive_state(store):
    store.put_config("rule", "busy", rule().model_dump())
    process_decision(store, decision(1))
    process_decision(store, decision(2))
    store.put_config("rule", "busy", rule().model_dump())
    assert process_decision(store, decision(3)) == []


@pytest.mark.parametrize("middle", [decision(2, probability=.1), decision(2, revision=2), decision(2, at=1100)])
def test_false_revision_and_gap_break_consecutive_matches(store, middle):
    store.put_config("rule", "busy", rule().model_dump())
    process_decision(store, decision(1))
    process_decision(store, middle)
    assert process_decision(store, decision(3, at=middle["captured_at"] + 2)) == []


def test_out_of_order_does_not_trigger(store):
    store.put_config("rule", "busy", rule().model_dump())
    process_decision(store, decision(2))
    process_decision(store, decision(1))
    assert process_decision(store, decision(3)) == []


def test_rest_persistence_and_validation(store):
    with TestClient(create_app(store, start_workers=False)) as client:
        cfg = {"rtsp_url": "rtsp://camera/live", "question": {"type": "score", "instructions": "How busy?", "criteria": ["Very busy", "Busy", "Not Busy", "Empty"]}}
        assert client.put("/v1/streams/beach", json=cfg).json()["revision"] == 1
        response = client.patch("/v1/streams/beach", json={"interval_seconds": .5})
        assert response.json()["revision"] == 1
        assert response.json()["config"]["question"]["criteria"][0] == "Very busy"
        assert client.patch("/v1/streams/beach", json={"interval_seconds": 0}).status_code == 422
        question = {"type": "noul", "instructions": "Is a lifeguard visible?"}
        assert client.patch("/v1/streams/beach", json={"question": question}).json()["revision"] == 2
    with TestClient(create_app(store, start_workers=False)) as client:
        assert client.get("/v1/streams").json()[0]["config"]["interval_seconds"] == .5


def test_schema_rejects_bad_threshold_and_criteria():
    with pytest.raises(ValueError):
        Rule(stream_id="beach", config_revision=1, field="score", value="high")
    with pytest.raises(ValueError):
        Question(type="score", instructions="How busy?", criteria=["Busy", "Busy"])


def test_review_sends_clip_and_correlates_result(store, monkeypatch):
    monkeypatch.setenv("COSMOS_BASE_URL", "http://cosmos")
    monkeypatch.setenv("COSMOS_MODEL", "cosmos-test")
    store.put_config("rule", "busy", rule(consecutive_samples=1).model_dump())
    event = process_decision(store, decision(1))[0]

    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, content=b"test-video-bytes")
        if request.method == "DELETE":
            return httpx.Response(200)
        if request.url.path == "/v1/files":
            assert b"test-video-bytes" in request.content
            return httpx.Response(200, json={"id": "uploaded-clip"})
        body = json.loads(request.content)
        assert body["id"] == "uploaded-clip"
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"verdict": "confirmed", "summary": "The beach is busy.", "evidence": ["People cluster near the shoreline."], "uncertainty": []})}}]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = review_event(event, client, lambda _: {"media_url": "http://vios/clip.mp4"})
    assert result["event_id"] == event["id"]
    assert result["decision_ids"] == ["1"]


def test_index_ids_are_stable_and_errors_propagate():
    urls = []

    def handle(request):
        urls.append(str(request.url))
        return httpx.Response(201)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        index_document(client, "http://es", decision(1))
        index_document(client, "http://es", decision(1))
    assert urls[0] == urls[1]
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            index_document(client, "http://es", decision(1))


def test_empty_cosmos_response_is_not_published(store, monkeypatch):
    monkeypatch.setenv("COSMOS_BASE_URL", "http://cosmos")
    monkeypatch.setenv("COSMOS_MODEL", "cosmos-test")
    store.put_config("rule", "busy", rule(consecutive_samples=1).model_dump())
    event = process_decision(store, decision(1))[0]
    deleted = []

    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, content=b"test-video-bytes")
        if request.method == "DELETE":
            deleted.append(True)
            return httpx.Response(200)
        if request.url.path == "/v1/files":
            return httpx.Response(200, json={"id": "uploaded-clip"})
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="no review text"):
            review_event(event, client, lambda _: {"media_url": "http://vios/clip.mp4"})
    assert deleted == [True, True]


def test_review_clip_is_cached_before_cosmos_inference(store, monkeypatch, tmp_path):
    from agent_pipeline.clips import clip_path
    monkeypatch.setenv("D1_CLIP_CACHE", str(tmp_path / "clips"))
    monkeypatch.setenv("COSMOS_BASE_URL", "http://cosmos")
    monkeypatch.setenv("COSMOS_MODEL", "test")
    store.put_config("rule", "busy", rule(consecutive_samples=1).model_dump())
    event = process_decision(store, decision(1))[0]
    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, content=b"exact-reviewed-video")
        if request.url.path == "/v1/files":
            assert clip_path(event["id"]).read_bytes() == b"exact-reviewed-video"
            return httpx.Response(200, json={"id": "asset"})
        if request.method == "DELETE":
            return httpx.Response(200)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"verdict": "confirmed", "summary": "The beach is busy.", "evidence": ["People are closely spaced."], "uncertainty": []})}}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        review_event(event, client, lambda _: {"media_url": "http://vios/clip"})
    from agent_pipeline.analytics import create_app as analytics_app
    with TestClient(analytics_app(store, start_workers=False)) as client:
        response = client.get(f'/v1/reviews/{event["id"]}/clip', headers={"Range": "bytes=0-4"})
        assert response.status_code == 206
        assert response.content == b"exact"
        assert client.get('/v1/reviews/missing/clip').status_code == 404
        assert client.get('/v1/reviews').json()[0]['clip_ready'] is True


def test_pending_clip_not_ready_is_explicit(store, monkeypatch, tmp_path):
    from agent_pipeline.analytics import create_app as analytics_app
    monkeypatch.setenv("D1_CLIP_CACHE", str(tmp_path / "clips"))
    store.put_config("rule", "busy", rule(consecutive_samples=1).model_dump())
    event = process_decision(store, decision(1))[0]
    with TestClient(analytics_app(store, start_workers=False)) as client:
        assert client.get(f'/v1/reviews/{event["id"]}/clip').status_code == 409


@pytest.mark.parametrize('fails', [False, True])
def test_review_worker_reports_active_state_and_finishes_or_retries(store, monkeypatch, fails):
    from agent_pipeline import analytics
    store.put_config('rule', 'busy', rule(consecutive_samples=1).model_dump())
    event = process_decision(store, decision(1))[0]
    def review(body, **_kwargs):
        assert store.rows('SELECT status FROM jobs')[0]['status'] == 'reviewing'
        if fails:
            raise RuntimeError('temporary inference failure')
        return {'id': body['id'] + '-review', 'event_id': body['id'], 'stream_id': 'beach',
                'verification_version': 1, 'verdict': 'confirmed', 'summary': 'People crowd the beach.',
                'evidence': ['People are closely spaced'], 'uncertainty': [], 'reviewed_at': 1234}
    monkeypatch.setattr(analytics, 'review_event', review)
    class OneIteration:
        calls = 0
        def wait(self, _):
            self.calls += 1
            return self.calls > 1
    analytics.run_reviews(store, OneIteration())
    row = store.rows('SELECT status,attempts FROM jobs')[0]
    assert row['status'] == ('pending' if fails else 'complete')
    assert row['attempts'] == (1 if fails else 0)

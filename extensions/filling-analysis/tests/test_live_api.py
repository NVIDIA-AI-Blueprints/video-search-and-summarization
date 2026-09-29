# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.live import LiveProxy, live_router


@pytest.fixture
def setup(tmp_path):
    stream, session = str(uuid4()), str(uuid4())
    path = tmp_path / "streams.json"
    path.write_text(json.dumps({"schema_version": 1, "streams": {stream: {
        "name": "approved", "width": 1280, "height": 720, "fps": 24,
        "profile_id": "sammy-single-station", "url": "rtsp://private/secret"}}}))
    state = {"type": "status", "mode": "live", "session_id": session, "stream_id": stream,
             "status": "running", "summary": {"total": 1, "normal": 0, "underfill": 1},
             "measurement": {}, "epoch": 1, "clock": {"verified": False}}
    event = {"event_id": "event", "seq": 1, "mode": "live", "session_id": session,
             "stream_id": stream, "kind": "cycle.finalized", "status": "underfill",
             "epoch": 1, "track_id": session + ":epoch-1:cycle-297",
             "final_level": .44885, "reference_level": .72803, "measurement": {"tolerance": .05}}
    routes = {"status": state, "start": state, "stop": dict(state, status="stopped"),
              "events": dict(state, events=[event], next_seq=1)}
    registry = {"available": True, "sources": [{"stream_id": stream, "type": "sensor_rtsp", "state": "online", "name": "Sammy"}]}
    requests = []

    async def list_sources():
        return registry

    def handle(request):
        requests.append(request)
        data = routes[request.url.path.split("/")[-1]]
        if isinstance(data, int):
            return httpx.Response(data, json={"detail": "rtsp://private/secret"})
        if isinstance(data, bytes):
            return httpx.Response(200, content=data, headers={"Content-Type": "image/jpeg"})
        return httpx.Response(200, json=data)

    proxy = LiveProxy("http://worker", path, list_sources, httpx.MockTransport(handle), public_origin="http://vss.test")
    app = FastAPI()
    app.include_router(live_router(proxy))
    return TestClient(app), routes, registry, requests, stream, session


def test_sources_exclude_unapproved_and_hide_urls(setup):
    client, _, registry, _, stream, _ = setup
    registry["sources"].append({"stream_id": str(uuid4()), "type": "stream", "state": "online"})
    data = client.get("/api/live/sources").json()
    assert [x["stream_id"] for x in data["sources"]] == [stream]
    assert "rtsp" not in json.dumps(data)


def test_start_requires_online_registered_approved_source(setup):
    client, _, registry, requests, stream, _ = setup
    assert client.post("/api/live/start", json={"stream_id": str(uuid4())}).status_code == 422
    registry["sources"][0]["state"] = "offline"
    assert client.post("/api/live/start", json={"stream_id": stream}).status_code == 409
    assert not requests


def test_start_idempotency_token_forwarded(setup):
    client, _, _, requests, stream, _ = setup
    request_id = str(uuid4())
    assert client.post("/api/live/start", json={"stream_id": stream, "request_id": request_id}).status_code == 200
    assert json.loads(requests[-1].content)["request_id"] == request_id


def test_reject_arbitrary_url_and_unknown_fields(setup):
    client, _, _, requests, stream, _ = setup
    assert client.post("/api/live/start", json={"stream_id": stream, "url": "rtsp://other"}).status_code == 422
    assert not requests


def test_reject_cross_session_or_source_response(setup):
    client, routes, _, _, _, session = setup
    routes["status"]["session_id"] = str(uuid4())
    assert client.get("/api/live/status", params={"session_id": session}).status_code == 503


def test_error_does_not_expose_private_worker_data(setup):
    client, routes, _, _, _, session = setup
    routes["status"] = 404
    response = client.get("/api/live/status", params={"session_id": session})
    assert response.status_code == 404
    assert "rtsp" not in response.text


def test_reject_cross_session_event(setup):
    client, routes, _, _, _, session = setup
    routes["events"]["events"][0]["session_id"] = str(uuid4())
    assert client.get("/api/live/events", params={"session_id": session}).status_code == 503


def test_pending_evidence_and_bounded_queries(setup):
    client, routes, _, _, _, session = setup
    routes["events"]["events"][0]["video_evidences"] = [{"public_url": "http://unverified"}]
    response = client.post("/api/live/query", json={"session_id": session, "question": "underfills"})
    assert response.status_code == 200
    data = response.json()
    assert len(data["matches"]) == 1
    assert data["matches"][0]["video_evidences"] == []
    assert data["matches"][0]["evidence_status"] == "pending"
    assert "not volume" in data["answer"]
    assert client.get("/api/live/events", params={"session_id": session, "limit": 201}).status_code == 422


def test_event_cursor_preserved(setup):
    client, routes, _, requests, _, session = setup
    routes["events"]["events"] = []
    routes["events"]["next_seq"] = 10
    data = client.get("/api/live/events", params={"session_id": session, "after_seq": 10}).json()
    assert data["next_seq"] == 10
    assert requests[-1].url.params["after_seq"] == "10"


def test_websocket_cross_source_rejected(setup):
    client, routes, _, _, stream, session = setup
    routes["frame"] = dict(routes["status"], type="frame", frame_id=1, stream_id=str(uuid4()))
    with client.websocket_connect("/api/live/ws?session_id=" + session) as ws:
        assert ws.receive_json()["stream_id"] == stream
        assert ws.receive_json()["type"] == "error"


def test_unbound_references_do_not_choose_current_or_history(setup):
    client, routes, _, _, _, session = setup
    routes['status']['current'] = {'track_id': 'cycle-298', 'phase': 'settled', 'level': .5}
    for question in ['What happened with this bottle?', 'Compare these two bottles.']:
        result = client.post('/api/live/query', json={'session_id': session, 'question': question}).json()
        assert result['query_status'] == 'unsupported'
        assert result['matches'] == [] and result['snapshot_evidences'] == []
        assert result['requested_cycle_ids'] == []


def test_selected_pair_keeps_each_bottles_actual_verdict_and_no_other_evidence(setup):
    client, routes, _, _, _, session = setup
    underfill = routes['events']['events'][0]
    normal = dict(underfill, seq=2, event_id='normal', track_id=f'{session}:epoch-1:cycle-298',
                  status='normal', final_level=.72803, reason='Height settled within the lower fill limit.')
    unrelated = dict(underfill, seq=3, event_id='unrelated', track_id=f'{session}:epoch-1:cycle-299', status='overflow')
    routes['events']['events'] = [underfill, normal, unrelated]
    selected = [underfill['track_id'], normal['track_id']]
    result = client.post('/api/live/query', json={'session_id': session, 'question': 'Compare these two bottles.', 'cycle_ids': selected}).json()
    assert result['query_status'] == 'ok'
    assert [(row['status'], row['final_level']) for row in result['matches']] == [('underfill', .44885), ('normal', .72803)]
    assert result['selected_track_ids'] == sorted(selected)
    assert all(row['event_id'] != 'unrelated' for row in result['matches'])
    assert 'Bottle cycle-298: normal.' in result['answer']


def test_selected_current_identity_does_not_follow_a_later_bottle(setup):
    client, routes, _, _, _, session = setup
    routes['status']['current'] = {'track_id': 'cycle-298', 'phase': 'settled', 'provisional': True, 'level': .5}
    target = f'{session}:epoch-1:cycle-298'
    result = client.post('/api/live/query', json={'session_id': session, 'question': 'What happened with this bottle?', 'cycle_ids': [target]}).json()
    assert result['query_status'] == 'in_progress' and result['matches'] == []
    routes['status']['current']['track_id'] = 'cycle-299'
    later = client.post('/api/live/query', json={'session_id': session, 'question': 'What happened with this bottle?', 'cycle_ids': [target]}).json()
    assert later['query_status'] == 'not_found' and later['matches'] == []
    assert 'cycle-299' not in later['answer']


def test_selected_pair_requires_two_distinct_same_session_identities(setup):
    client, routes, _, _, _, session = setup
    target = routes['events']['events'][0]['track_id']
    one = client.post('/api/live/query', json={'session_id': session, 'question': 'Compare these two bottles.', 'cycle_ids': [target]}).json()
    assert one['query_status'] == 'unsupported' and one['matches'] == []
    duplicate = client.post('/api/live/query', json={'session_id': session, 'question': 'Compare these two bottles.', 'cycle_ids': [target, target]})
    assert duplicate.status_code == 422
    other = client.post('/api/live/query', json={'session_id': session, 'question': 'What happened with this bottle?', 'cycle_ids': [f'{uuid4()}:epoch-1:cycle-298']})
    assert other.status_code == 409


def test_latest_events_query_is_forwarded_without_reading_old_pages(setup):
    client, _, _, requests, _, session = setup
    response = client.get('/api/live/events', params={'session_id': session, 'latest': True, 'limit': 30})
    assert response.status_code == 200
    assert len(requests) == 1
    assert requests[0].url.params['latest'] == 'true'
    assert requests[0].url.params['limit'] == '30'


def test_explicit_labels_conflicting_with_selection_are_not_silently_reinterpreted(setup):
    client, routes, _, _, _, session = setup
    target = routes['events']['events'][0]['track_id']
    response = client.post('/api/live/query', json={'session_id': session, 'question': 'What happened with cycle-999?', 'cycle_ids': [target]})
    assert response.status_code == 409

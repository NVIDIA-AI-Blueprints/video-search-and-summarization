# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import httpx
import pytest
from fastapi.testclient import TestClient
from agent_pipeline import assistant
from agent_pipeline.analytics import create_app
from agent_pipeline.store import Store
from agent_pipeline.contracts import Rule


def draft():
    return {'question': {'type': 'noul', 'instructions': 'Is a vehicle visible on the beach?', 'criteria': None},
            'interval_seconds': 3, 'review': {'field': 'noul', 'probability_key': None, 'operator': 'gte',
            'value': .8, 'consecutive_samples': 3, 'max_gap_seconds': 10, 'cooldown_seconds': 60,
            'pre_seconds': 10, 'post_seconds': 5, 'review_prompt': 'Describe visible vehicles and their movement.'}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('NEMOTRON_URL', 'http://prompt-assistant:8019')
    store = Store(tmp_path / 'analytics.sqlite')
    store.put_config('rule', 'busy', Rule(stream_id='beach', config_revision=6, **draft()['review']).model_dump())
    current = {'id': 'beach', 'revision': 6, 'config': {'rtsp_url': 'rtsp://camera/live', 'vios_sensor': 'beach',
        'question': {'type': 'score', 'instructions': 'How crowded?', 'criteria': ['Busy', 'Empty']},
        'interval_seconds': 2, 'enabled': True}}
    return store, current


def test_rejects_incompatible_or_out_of_range_trigger():
    for changed in [dict(field='score'), dict(value=3), dict(max_gap_seconds=1)]:
        value = draft(); value['review'].update(changed)
        with pytest.raises(ValueError): assistant.MonitorDraft.model_validate(value)


def test_generation_is_draft_only_and_rejects_invalid_output(setup, monkeypatch):
    store, current = setup
    class Fake:
        invalid = False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, json):
            assert url.endswith('/v1/chat/completions')
            assert json['response_format']['type'] == 'json_schema'
            answer = draft()
            if self.invalid: answer['review']['field'] = 'score'
            return httpx.Response(200, request=httpx.Request('POST', url), json={'choices': [{'finish_reason': 'stop', 'message': {'content': __import__('json').dumps({'reply': 'Draft ready.', 'draft': answer})}}]})
    fake = Fake()
    with TestClient(create_app(store, start_workers=False)) as client:
        monkeypatch.setattr(assistant.httpx, 'Client', lambda **kw: fake)
        before = store.configs('rule')
        request = {'messages': [{'role': 'user', 'content': 'Watch for vehicles'}], 'current': draft()}
        assert client.post('/v1/assistant/draft', json=request).status_code == 200
        fake.invalid = True
        assert client.post('/v1/assistant/draft', json=request).status_code == 502
        assert store.configs('rule') == before
        request['messages'][0]['role'] = 'system'
        assert client.post('/v1/assistant/draft', json=request).status_code == 422


@pytest.mark.parametrize('failure', [False, True])
def test_apply_disables_old_rule_then_rebinds_or_leaves_disabled(setup, monkeypatch, failure):
    store, current = setup
    class Fake:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url): return httpx.Response(200, request=httpx.Request('GET', url), json=[current])
        def put(self, url, json, headers):
            assert store.configs('rule')[0]['config']['enabled'] is False
            assert headers == {'If-Match': '6'}
            assert json['rtsp_url'] == current['config']['rtsp_url']
            assert json['enabled'] is True
            if failure: raise httpx.ConnectError('offline')
            return httpx.Response(200, request=httpx.Request('PUT', url), json={'id': 'beach', 'revision': 7, 'config': json})
    with TestClient(create_app(store, start_workers=False)) as client:
        monkeypatch.setattr(assistant.httpx, 'Client', lambda **kw: Fake())
        payload = {'stream_id': 'beach', 'rule_id': 'busy', 'stream_revision': 6, 'rule_revision': 1, 'draft': draft()}
        stale = client.post('/v1/assistant/apply', json={**payload, 'stream_revision': 5})
        assert stale.status_code == 409 and store.configs('rule')[0]['revision'] == 1
        response = client.post('/v1/assistant/apply', json=payload)
        assert response.status_code == (503 if failure else 200)
        rule = store.configs('rule')[0]['config']
        assert rule['enabled'] is (not failure)
        if not failure:
            assert rule['config_revision'] == 7
            assert rule['review_prompt'] == draft()['review']['review_prompt']


def test_ingest_if_match_prevents_stale_semantic_updates(tmp_path):
    from agent_pipeline.ingest import create_app as ingest_app
    with TestClient(ingest_app(Store(tmp_path / 'ingest.sqlite'), start_workers=False)) as client:
        config = {'rtsp_url': 'rtsp://camera/live', 'question': draft()['question']}
        assert client.put('/v1/streams/beach', json=config).status_code == 200
        assert client.put('/v1/streams/beach', json=config, headers={'If-Match': '9'}).status_code == 409
        assert client.put('/v1/streams/beach', json=config, headers={'If-Match': '1'}).status_code == 200


def test_video_source_capability_is_read_from_server_and_apply_preserves_it(setup, monkeypatch):
    store, current = setup
    current['config'].update(input_mode='video', video_frames=8, video_fps=1)
    class Fake:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url):
            assert url.endswith('/v1/streams')
            return httpx.Response(200, request=httpx.Request('GET', url), json=[current])
        def post(self, url, json):
            system = json['messages'][0]['content']
            context = system.split('INPUT CONFIGURATION:\n', 1)[1].split('\nCURRENT DRAFT:', 1)[0]
            assert __import__('json').loads(context)['input_mode'] == 'video'
            assert __import__('json').loads(context)['video_frames'] == 8
            assert 'rtsp://' not in system
            return httpx.Response(200, request=httpx.Request('POST', url), json={'choices': [
                {'finish_reason': 'stop', 'message': {'content': __import__('json').dumps({'reply': 'Draft ready', 'draft': draft()})}}]})
        def put(self, url, json, headers):
            assert json['input_mode'] == 'video'
            assert json['video_frames'] == 8
            assert json['video_fps'] == 1
            return httpx.Response(200, request=httpx.Request('PUT', url), json={'id': 'beach', 'revision': 7, 'config': json})
    with TestClient(create_app(store, start_workers=False)) as client:
        monkeypatch.setattr(assistant.httpx, 'Client', lambda **kwargs: Fake())
        response = client.post('/v1/assistant/draft', json={'stream_id': 'beach', 'current': draft(),
            'messages': [{'role': 'user', 'content': 'Watch for slow traffic'}]})
        assert response.status_code == 200
        response = client.post('/v1/assistant/apply', json={'stream_id': 'beach', 'rule_id': 'busy',
            'stream_revision': 6, 'rule_revision': 1, 'draft': draft()})
        assert response.status_code == 200


def test_unconfigured_prompt_assistant_does_not_probe_network(setup, monkeypatch):
    store, _ = setup
    monkeypatch.delenv("NEMOTRON_URL", raising=False)
    def unexpected_request(*args, **kwargs):
        pytest.fail("disabled assistant must not issue a request")
    monkeypatch.setattr(assistant.httpx, "get", unexpected_request)
    monkeypatch.setattr(assistant.httpx, "Client", unexpected_request)
    with TestClient(create_app(store, start_workers=False)) as client:
        assert client.get("/v1/assistant/health").json()["ready"] is False
        request = {"messages": [{"role": "user", "content": "Watch for vehicles"}], "current": draft()}
        assert client.post("/v1/assistant/draft", json=request).status_code == 503

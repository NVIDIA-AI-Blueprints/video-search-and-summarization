# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_pipeline import demo_sources
from agent_pipeline.contracts import Rule
from agent_pipeline.store import Store


def prepare(tmp_path, monkeypatch):
    store = Store(tmp_path / 'analytics.sqlite')
    streams = {}
    for source, _, stream, rule in demo_sources.SOURCES:
        streams[stream] = {'id': stream, 'revision': 2, 'config': {
            'rtsp_url': 'rtsp://private-source/' + stream, 'question': {'type': 'noul', 'instructions': 'Is there a queue?'},
            'enabled': source == 'flowing'}}
        store.put_config('rule', rule, Rule(stream_id=stream, config_revision=2, field='noul', value=.8,
                                          enabled=source == 'flowing').model_dump())
    class Ingest:
        fail = False
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url):
            return httpx.Response(200, request=httpx.Request('GET', url), json=list(streams.values()))
        def put(self, url, json, headers):
            item = streams[url.rsplit('/', 1)[-1]]
            assert headers['If-Match'] == str(item['revision'])
            if self.fail and item['id'] == 'd1-queued' and json['enabled']:
                raise httpx.ConnectError('test unavailable')
            item['config'] = copy.deepcopy(json)
            return httpx.Response(200, request=httpx.Request('PUT', url), json=item)
    monkeypatch.setattr(demo_sources.httpx, 'Client', Ingest)
    monkeypatch.setenv('D1_QUEUED_KIND', 'recorded')
    monkeypatch.setenv('D1_SELECTION_BACKUPS', str(tmp_path / 'snapshots'))
    app = FastAPI(); app.include_router(demo_sources.router(store))
    return TestClient(app), store, streams, Ingest


def test_source_switch_preserves_prompts_and_marks_recording(tmp_path, monkeypatch):
    client, store, streams, _ = prepare(tmp_path, monkeypatch)
    original = copy.deepcopy(streams)
    response = client.get('/v1/demo/sources')
    assert response.json()['active_source'] == 'flowing'
    assert 'rtsp://' not in response.text
    result = client.post('/v1/demo/sources', json={'source_id': 'queued'})
    assert result.status_code == 200 and result.json()['active_source'] == 'queued'
    assert 'Recorded footage' in result.json()['message']
    for key in streams:
        assert streams[key]['revision'] == original[key]['revision']
        assert {k:v for k,v in streams[key]['config'].items() if k != 'enabled'} == {k:v for k,v in original[key]['config'].items() if k != 'enabled'}
    assert [r['id'] for r in store.configs('rule') if r['config']['enabled']] == ['d1-queued']
    assert list((tmp_path / 'snapshots').glob('*.json'))
    assert client.post('/v1/demo/sources', json={'source_id': 'http://external'}).status_code == 422


def test_partial_source_failure_restores_previous_source(tmp_path, monkeypatch):
    client, store, streams, ingest = prepare(tmp_path, monkeypatch)
    original = copy.deepcopy(streams)
    before_rules = {r['id']: r['config'] for r in store.configs('rule')}
    ingest.fail = True
    assert client.post('/v1/demo/sources', json={'source_id': 'queued'}).status_code == 503
    assert streams == original
    assert {r['id']: r['config'] for r in store.configs('rule')} == before_rules


def test_source_revision_mismatch_never_switches(tmp_path, monkeypatch):
    client, _, streams, _ = prepare(tmp_path, monkeypatch)
    streams['d1-queued']['revision'] = 99
    before = copy.deepcopy(streams)
    assert client.post('/v1/demo/sources', json={'source_id': 'queued'}).status_code == 409
    assert streams == before


def test_unprepared_queued_input_cannot_change_active_source(tmp_path, monkeypatch):
    client, _, streams, _ = prepare(tmp_path, monkeypatch)
    del streams['d1-queued']
    result = client.get('/v1/demo/sources')
    source = next(s for s in result.json()['sources'] if s['id'] == 'queued')
    assert source['kind'] == 'recorded' and not source['available'] and not source['active']
    before = copy.deepcopy(streams)
    assert client.post('/v1/demo/sources', json={'source_id': 'queued'}).status_code == 409
    assert streams == before
    assert client.get('/v1/demo/sources').json()['active_source'] == 'flowing'

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Acceptance at the worker/API boundary: a verdict changes operator visibility."""
import json

import pytest
from fastapi.testclient import TestClient

from agent_pipeline import analytics
from agent_pipeline.contracts import Rule
from agent_pipeline.rules import process_decision
from agent_pipeline.store import Store
from agent_pipeline.verification import VerificationFormatError


def candidate(store):
    store.put_config('rule', 'queue', Rule(stream_id='traffic', config_revision=1, field='noul',
                                         value=.8, consecutive_samples=1).model_dump())
    return process_decision(store, {'schema_version': 1, 'kind': 'd1.decision', 'id': 'sample',
        'stream_id': 'traffic', 'config_revision': 1, 'captured_at': 1000,
        'question': {'type': 'noul', 'instructions': 'Is there a queue?'}, 'answer': {'noul': .95}})[0]


class OneIteration:
    calls = 0
    def wait(self, _):
        self.calls += 1
        return self.calls > 1


@pytest.mark.parametrize('verdict', ['confirmed', 'rejected', 'uncertain'])
def test_worker_publishes_correlated_verdict_and_gates_incident(tmp_path, monkeypatch, verdict):
    store = Store(tmp_path / 'analytics.sqlite')
    event = candidate(store)
    def review(body, **_kwargs):
        return {'id': body['id'] + '-review', 'event_id': body['id'], 'stream_id': 'traffic',
                'kind': 'd1.review', 'schema_version': 1, 'verification_version': 1, 'verdict': verdict,
                'summary': 'Vehicles show the described motion.', 'evidence': ['Visible movement is assessed across the video.'],
                'uncertainty': ['Some traffic is outside the camera view.'] if verdict == 'uncertain' else []}
    monkeypatch.setattr(analytics, 'review_event', review)
    analytics.run_reviews(store, OneIteration())
    with TestClient(analytics.create_app(store, start_workers=False)) as client:
        projection = client.get('/v1/incidents').json()
        assert len(projection['incidents']) == (1 if verdict == 'confirmed' else 0)
        assert projection['candidates'][0]['status'] == verdict
        assert projection['candidates'][0]['event_id'] == event['id']
    rows = store.rows('SELECT body FROM outbox WHERE topic=?', ('d1.reviews.v1',))
    assert len(rows) == 1 and json.loads(rows[0]['body'])['verdict'] == verdict
    assert store.rows('SELECT status FROM jobs')[0]['status'] == 'complete'


def test_invalid_verification_does_not_become_rejection_or_success(tmp_path, monkeypatch):
    store = Store(tmp_path / 'analytics.sqlite'); candidate(store)
    def invalid(_, **_kwargs):
        error = VerificationFormatError('Bad model JSON', errors=[{
            'field': 'uncertainty[0]', 'code': 'observation_placeholder',
            'message': 'Use a concrete limitation or an empty list.'}])
        error.last_response = {'private_untrusted_prose': 'Do not store raw text here.'}
        raise error
    monkeypatch.setattr(analytics, 'review_event', invalid)
    analytics.run_reviews(store, OneIteration())
    with TestClient(analytics.create_app(store, start_workers=False)) as client:
        result = client.get('/v1/incidents').json()
    assert result['incidents'] == []
    assert result['counts']['rejected'] == 0
    assert result['candidates'][0]['status'] == 'failed'
    assert result['candidates'][0]['error'] == 'VerificationFormatError'
    assert store.rows("SELECT * FROM outbox WHERE topic='d1.reviews.v1'") == []
    assert store.rows('SELECT attempts FROM jobs')[0]['attempts'] == 1
    saved = json.loads(store.rows('SELECT body FROM review_diagnostics')[0]['body'])
    assert saved['validation_errors'][0]['field'] == 'uncertainty[0]'
    assert 'private_untrusted_prose' not in json.dumps(saved)
    # No automatic rerun after the bounded corrective generation has failed.
    analytics.run_reviews(store, OneIteration())
    assert store.rows('SELECT attempts FROM jobs')[0]['attempts'] == 1


def test_network_failure_remains_retryable_without_publishing_verdict(tmp_path, monkeypatch):
    import httpx
    store = Store(tmp_path / 'analytics.sqlite'); candidate(store)
    def unavailable(_, **_kwargs): raise httpx.ConnectError('Model temporarily unavailable')
    monkeypatch.setattr(analytics, 'review_event', unavailable)
    analytics.run_reviews(store, OneIteration())
    assert store.rows('SELECT status,attempts,error FROM jobs')[0] == {
        'status': 'pending', 'attempts': 1, 'error': 'ConnectError'}
    assert store.rows("SELECT * FROM outbox WHERE topic='d1.reviews.v1'") == []


def test_changed_review_evidence_is_terminal_and_explicitly_retryable(tmp_path, monkeypatch):
    store = Store(tmp_path / 'analytics.sqlite'); event = candidate(store)
    def changed(_, **_kwargs): raise analytics.ClipEvidenceChanged('Evidence changed')
    monkeypatch.setattr(analytics, 'review_event', changed)
    analytics.run_reviews(store, OneIteration())
    assert store.rows('SELECT status,error FROM jobs')[0] == {
        'status': 'failed', 'error': 'ClipEvidenceChanged'}
    with TestClient(analytics.create_app(store, start_workers=False)) as client:
        assert client.post('/v1/reviews/' + event['id'] + '/retry').status_code == 200
    assert store.rows('SELECT status,attempts FROM jobs')[0] == {'status': 'pending', 'attempts': 0}


def test_projection_and_kafka_outbox_commit_together(tmp_path, monkeypatch):
    store = Store(tmp_path / 'analytics.sqlite'); candidate(store)
    monkeypatch.setattr(analytics, 'review_event', lambda e, **_kwargs: {
        'id': e['id']+'-review', 'event_id': e['id'], 'stream_id': 'traffic', 'verification_version': 1,
        'verdict': 'confirmed', 'summary': 'A stationary queue is visible.', 'evidence': ['The vehicles do not advance.'], 'uncertainty': []})
    def fail_enqueue(*args): raise OSError('Simulated durable publication failure')
    monkeypatch.setattr(store, 'enqueue', fail_enqueue)
    analytics.run_reviews(store, OneIteration())
    assert store.rows('SELECT verdict,review FROM incident_candidates')[0] == {'verdict': None, 'review': None}
    assert store.rows('SELECT status FROM jobs')[0]['status'] == 'pending'


def test_shutdown_wait_cancellation_keeps_review_pending_without_failure(tmp_path, monkeypatch):
    store = Store(tmp_path / 'analytics.sqlite'); candidate(store)
    stop = OneIteration()
    def cancelled(_event, **kwargs):
        assert kwargs['stop'] is stop
        raise analytics.InferenceCancelled('Shutdown while waiting')
    monkeypatch.setattr(analytics, 'review_event', cancelled)
    analytics.run_reviews(store, stop)
    assert store.rows('SELECT status,attempts,error FROM jobs')[0] == {
        'status': 'pending', 'attempts': 0, 'error': None}
    assert store.rows("SELECT * FROM outbox WHERE topic='d1.reviews.v1'") == []

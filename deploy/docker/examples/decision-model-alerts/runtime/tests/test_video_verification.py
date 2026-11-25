# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests of the evidence boundary, verdict validation, and video-bound questions."""
import hashlib
import json
from copy import deepcopy
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest
from agent_pipeline.clips import clip_path
from agent_pipeline.review import review_event, INFERENCE_LOCK
from agent_pipeline.store import Store
from agent_pipeline.verification import candidate_condition, parse_verification, VerificationFormatError
from agent_pipeline import video_qa


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv('D1_CLIP_CACHE', str(tmp_path / 'clips'))
    monkeypatch.setenv('COSMOS_BASE_URL', 'http://cosmos')
    monkeypatch.setenv('COSMOS_MODEL', 'test-cosmos')
    monkeypatch.setenv('COSMOS_API_KEY', 'private-model-key')


@pytest.fixture
def event():
    return {'id': 'event-a', 'stream_id': 'camera-a', 'captured_at': 1000,
            'decision_ids': ['decision-a'], 'clip_start': 985, 'clip_end': 1005,
            'trigger': {'question': {'type': 'noul', 'instructions': 'Are vehicles stopped or crawling?'},
                        'answer': {'noul': .99}, 'vios_sensor': 'traffic'},
            'rule': {'field': 'noul', 'operator': 'gte', 'value': .8,
                     'review_prompt': 'Describe where the queue is.'}}


def verdict(value='confirmed'):
    return {'verdict': value, 'summary': 'The visible queue is barely moving.',
            'evidence': ['The closely spaced cars change position very little.'],
            'uncertainty': ['The road beyond the bend is outside view.']}


def transport_for(content, calls):
    def handle(request):
        calls.append(request)
        if request.method == 'GET':
            assert 'Authorization' not in request.headers
            return httpx.Response(200, content=b'exact-candidate-video')
        if request.method == 'DELETE':
            return httpx.Response(200)
        if request.url.path == '/v1/files':
            assert b'exact-candidate-video' in request.content
            assert request.headers['Authorization'] == 'Bearer private-model-key'
            return httpx.Response(200, json={'id': 'upload-1'})
        return httpx.Response(200, json={'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]})
    return httpx.MockTransport(handle)


@pytest.mark.parametrize('value', ['confirmed', 'rejected', 'uncertain'])
def test_real_verdict_is_validated_and_bound_to_saved_question_and_bytes(event, value):
    calls = []
    with httpx.Client(transport=transport_for(json.dumps(verdict(value)), calls)) as client:
        result = review_event(event, client, lambda _: {'media_url': 'http://vios/clip'})
    assert result['verdict'] == value
    assert result['clip_sha256'] == hashlib.sha256(b'exact-candidate-video').hexdigest()
    assert result['condition'] == {'question': event['trigger']['question'], 'expected_answer': 'yes'}
    body = json.loads(next(r.content for r in calls if r.url.path == '/v1/chat/completions'))
    assert body['response_format']['type'] == 'json_schema'
    assert body['enable_reasoning'] is False
    assert '0.99' not in json.dumps(body['messages'])
    assert event['trigger']['question']['instructions'] in body['messages'][1]['content']
    assert calls[-1].method == 'DELETE'


@pytest.mark.parametrize('content', [
    'There is probably traffic.', '{"verdict":true}',
    json.dumps({**verdict(), 'verdict': 'yes'}),
    json.dumps({**verdict(), 'evidence': []}),
    json.dumps({**verdict(), 'evidence': [',']}),
    json.dumps({**verdict(), 'evidence': ['..............']}),
    json.dumps({**verdict('uncertain'), 'uncertainty': []}),
    json.dumps({**verdict(), 'summary': '   '}),
    json.dumps({**verdict(), 'dispatch': False}),
])
def test_bad_model_output_never_becomes_confirmed_or_rejected(event, content):
    calls = []
    with httpx.Client(transport=transport_for(content, calls)) as client:
        with pytest.raises(VerificationFormatError):
            review_event(event, client, lambda _: {'media_url': 'http://vios/clip'})
    assert calls[-1].method == 'DELETE'
    assert clip_path(event['id']).read_bytes() == b'exact-candidate-video'
    assert sum(r.url.path == '/v1/chat/completions' for r in calls) == 2


def test_retry_reuses_exact_original_clip_without_resolving_again(event):
    clip_path(event['id']).write_bytes(b'exact-candidate-video')
    calls = []
    def forbidden(_):
        pytest.fail('An existing event clip must not be replaced on retry')
    with httpx.Client(transport=transport_for(json.dumps(verdict()), calls)) as client:
        result = review_event(event, client, forbidden)
    assert not any(r.method == 'GET' for r in calls)
    assert result['clip']['source'] == 'event_clip_cache'


def test_fenced_object_is_allowed_but_extraneous_prose_is_not():
    assert parse_verification('```json\n' + json.dumps(verdict()) + '\n```')['verdict'] == 'confirmed'
    with pytest.raises(VerificationFormatError):
        parse_verification('Verified! ' + json.dumps(verdict()))


def test_negative_question_and_class_conditions_are_preserved(event):
    event['rule'].update(operator='lte', value=.2)
    assert candidate_condition(event)['expected_answer'] == 'no'
    event['trigger']['question'] = {'type': 'score', 'instructions': 'How busy?', 'criteria': ['Crowded', 'Quiet']}
    event['rule'].update(field='probability', probability_key='1', operator='gte', value=.8)
    assert candidate_condition(event)['expected_answer'] == 'Quiet'
    event['rule'].update(field='score', operator='lte', value=0)
    assert candidate_condition(event)['expected_answer'] == 'One of these categories: Crowded'


def test_missing_historical_question_does_not_invent_verification(event):
    del event['trigger']['question']
    with pytest.raises(VerificationFormatError, match='saved D1 question'):
        candidate_condition(event)


@pytest.fixture
def qa_app(tmp_path, event):
    store = Store(tmp_path / 'qa.sqlite')
    with store.transaction() as db:
        db.execute('INSERT INTO jobs VALUES(?,?,?,0,?,NULL)',
                   (event['id'], 1007, 'complete', json.dumps(event)))
    app = FastAPI()
    queue = video_qa.QuestionQueue(store)
    app.state.question_queue = queue
    app.include_router(video_qa.router(store, queue=queue))
    yield app, store
    queue.stop()


def test_question_uploads_exact_video_and_never_uses_caption_only(event):
    clip_path(event['id']).write_bytes(b'exact-candidate-video')
    calls = []
    with httpx.Client(transport=transport_for(json.dumps({'answer': 'No collision is visible in this clip.', 'limitations': ['Events outside the camera view cannot be assessed.']}), calls)) as client:
        result = video_qa.answer_video_question(event, 'Is there a collision?', [], client=client)
    assert result['answer'] == 'No collision is visible in this clip. Events outside the camera view cannot be assessed.'
    assert result['limitations'] == ['Events outside the camera view cannot be assessed.']
    assert result['clip_sha256'] == hashlib.sha256(b'exact-candidate-video').hexdigest()
    assert any(r.url.path == '/v1/files' for r in calls)
    assert not any(r.method == 'GET' for r in calls)
    body = json.loads(next(r.content for r in calls if r.url.path == '/v1/chat/completions'))
    assert body['messages'][-1]['content'] == 'Is there a collision?'


def test_question_persistence_idempotency_and_event_scoping(qa_app, event, monkeypatch):
    app, store = qa_app
    clip_path(event['id']).write_bytes(b'exact-candidate-video')
    observed = []
    def answer(saved, question, history, **kwargs):
        observed.append((saved, question, history))
        return {'answer': 'No collision is visible.', 'clip_sha256': kwargs['expected_sha256'], 'model': 'cosmos'}
    monkeypatch.setattr(video_qa, 'answer_video_question', answer)
    with TestClient(app) as client:
        payload = {'question': 'Is there a collision?', 'request_id': 'request-1'}
        first = client.post('/v1/reviews/event-a/questions', json=payload)
        assert first.status_code == 202
        assert first.json()['status'] == 'queued'
        assert app.state.question_queue.run_once()
        first = client.get('/v1/reviews/event-a/questions/request-1')
        assert first.json()['status'] == 'complete'
        assert client.post('/v1/reviews/event-a/questions', json=payload).json() == first.json()
        assert len(observed) == 1
        assert client.post('/v1/reviews/event-a/questions', json={**payload, 'question': 'Different?'}).status_code == 409
        assert client.post('/v1/reviews/event-a/questions', json={'question': 'Where?'}).status_code == 202
        assert app.state.question_queue.run_once()
        assert observed[-1][2][0]['answer'] == 'No collision is visible.'
        second = deepcopy(event)
        second['id'] = 'event-b'
        second['stream_id'] = 'camera-b'
        with store.transaction() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,0,?,NULL)', ('event-b', 1007, 'complete', json.dumps(second)))
        clip_path('event-b').write_bytes(b'another-video')
        assert client.get('/v1/reviews/event-b/questions').json() == []
        assert client.post('/v1/reviews/event-b/questions', json=payload).status_code == 202
        assert app.state.question_queue.run_once()
        assert observed[-1][0]['id'] == 'event-b'
        assert observed[-1][2] == []
    restarted = FastAPI()
    restarted.include_router(video_qa.router(store))
    with TestClient(restarted) as client:
        history = client.get('/v1/reviews/event-a/questions').json()
        assert len(history) == 2
        assert all(x['event_id'] == 'event-a' and x['clip_start'] == 985 for x in history)


def test_questions_fail_clearly_without_video_when_busy_and_on_model_failure(qa_app, event, monkeypatch):
    app, store = qa_app
    with TestClient(app) as client:
        assert client.post('/v1/reviews/missing/questions', json={'question': 'Collision?'}).status_code == 404
        assert client.post('/v1/reviews/event-a/questions', json={'question': 'Collision?'}).status_code == 410
        assert client.post('/v1/reviews/event-a/questions', json={'question': '  '}).status_code == 422
        assert client.post('/v1/reviews/event-a/questions', json={'question': 'x' * 1201}).status_code == 422
        with store.transaction() as db:
            db.execute("UPDATE jobs SET status='pending'")
        assert client.post('/v1/reviews/event-a/questions', json={'question': 'Collision?'}).status_code == 409
        clip_path(event['id']).write_bytes(b'exact-candidate-video')
        INFERENCE_LOCK.acquire()
        try:
            response = client.post('/v1/reviews/event-a/questions', json={'question': 'Collision?', 'request_id': 'busy-q'})
            assert response.status_code == 202
            assert response.json()['status'] == 'queued'
        finally:
            INFERENCE_LOCK.release()
        def fail(*args, **kwargs):
            raise ValueError('invalid response')
        monkeypatch.setattr(video_qa, 'answer_video_question', fail)
        assert app.state.question_queue.run_once()
        response = client.get('/v1/reviews/event-a/questions/busy-q')
        assert response.json()['status'] == 'failed'
        assert response.json()['error']['code'] == 'invalid_answer'
        assert 'answer' not in response.json()
        assert client.get('/v1/reviews/event-a/questions').json()[0]['status'] == 'failed'
        assert not INFERENCE_LOCK.locked()


def test_changed_cached_video_is_not_used_as_original_evidence(event):
    clip_path(event['id']).write_bytes(b'exact-candidate-video')
    calls = []
    with httpx.Client(transport=transport_for('No collision.', calls)) as client:
        with pytest.raises(ValueError, match='no longer matches'):
            video_qa.answer_video_question(event, 'Collision?', [], client=client, expected_sha256='wrong-hash')
    assert calls == []


def test_one_corrective_generation_keeps_the_same_evidence(event):
    calls = []
    completions = []
    base = transport_for(json.dumps(verdict()), calls)
    def handler(request):
        if request.url.path == '/v1/chat/completions':
            body = json.loads(request.content)
            completions.append(body)
            content = {**verdict(), 'evidence': [',']} if len(completions) == 1 else verdict('rejected')
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(content)}}]})
        return base.handle_request(request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = review_event(event, client, lambda _: {'media_url': 'http://vios/clip'})
    assert result['verdict'] == 'rejected'
    assert result['verification_attempts'] == 2
    assert len(completions) == 2
    assert 'failed validation' in completions[1]['messages'][-1]['content']
    assert sum(r.method == 'GET' for r in calls) == 1
    assert sum(r.url.path == '/v1/files' for r in calls) == 2
    assert result['clip_sha256'] == hashlib.sha256(b'exact-candidate-video').hexdigest()


def test_model_decoration_is_removed_without_changing_observations():
    body = verdict()
    body['summary'] = '>>> The visible queue is barely moving.'
    body['evidence'] = ['>>> Closely spaced vehicles move very little.']
    body['uncertainty'] = ['>>> The distant road is outside view.']
    result = parse_verification(json.dumps(body))
    assert result['summary'] == 'The visible queue is barely moving.'
    assert result['evidence'] == ['Closely spaced vehicles move very little.']
    assert result['uncertainty'] == ['The distant road is outside view.']
    with pytest.raises(VerificationFormatError):
        parse_verification(json.dumps({**verdict(), 'evidence': ['An overly long observation. ' * 20]}))


@pytest.mark.parametrize('bad', ['No', json.dumps({'answer': 'No', 'limitations': []}),
    json.dumps({'answer': 'No collision is visible.', 'limitations': [',']}),
    json.dumps({'answer': 'No collision is visible.', 'limitations': []})])
def test_question_never_publishes_unusable_answer_or_missing_limits(event, bad):
    clip_path(event['id']).write_bytes(b'exact-candidate-video')
    calls = []
    with httpx.Client(transport=transport_for(bad, calls)) as client:
        with pytest.raises(ValueError, match='usable video answer'):
            video_qa.answer_video_question(event, 'Collision?', [], client=client)
    assert sum(r.url.path == '/v1/chat/completions' for r in calls) == 2
    assert sum(r.method == 'DELETE' for r in calls) == 2


def test_question_corrective_attempt_adds_meaningful_answer_and_limits(event):
    clip_path(event['id']).write_bytes(b'exact-candidate-video')
    calls = []
    completions = []
    base = transport_for('', calls)
    def handler(request):
        if request.url.path == '/v1/chat/completions':
            body = json.loads(request.content)
            completions.append(body)
            assert body['response_format']['json_schema']['name'] == 'event_video_answer'
            content = 'No' if len(completions) == 1 else json.dumps({
                'answer': '>>> No collision is visible in this clip.',
                'limitations': ['>>> The clip cannot establish what happens outside the view.']})
            return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})
        return base.handle_request(request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = video_qa.answer_video_question(event, 'Collision?', [], client=client)
    assert result['answer_attempts'] == 2
    assert result['answer'] == 'No collision is visible in this clip. The clip cannot establish what happens outside the view.'
    assert result['limitations'] == ['The clip cannot establish what happens outside the view.']
    assert result['clip_sha256'] == hashlib.sha256(b'exact-candidate-video').hexdigest()
    assert 'failed validation' in completions[1]['messages'][-1]['content']


@pytest.mark.parametrize('review_hash,older_hash', [('new-video', 'original-video'), ('original-video', 'different-older-video'), (None, 'different-older-video')])
def test_question_history_cannot_cross_evidence_versions(qa_app, event, monkeypatch, review_hash, older_hash):
    app, store = qa_app
    clip_path(event['id']).write_bytes(b'newer-video')
    with store.transaction() as db:
        # Include an old conflicting turn beyond the 30-turn display limit.
        for n in range(32):
            body = {'id': f'q-{n}', 'event_id': event['id'], 'question': 'What is visible?',
                    'answer': 'Vehicles are visible.', 'created_at': n,
                    'clip_sha256': older_hash if n == 0 else 'original-video'}
            db.execute('INSERT INTO event_questions VALUES(?,?,?,?)', (event['id'], body['id'], n, json.dumps(body)))
        if review_hash:
            db.execute('INSERT INTO incident_candidates VALUES(?,?,?,?)',
                       (event['id'], 'incident-a', 'confirmed', json.dumps({'clip_sha256': review_hash})))
    def forbidden(*args, **kwargs):
        pytest.fail('Conflicting video history must never reach Cosmos')
    monkeypatch.setattr(video_qa, 'answer_video_question', forbidden)
    with TestClient(app) as client:
        response = client.post('/v1/reviews/event-a/questions', json={'question': 'What happens next?'})
        assert response.status_code == 410
        assert 'evidence changed' in response.json()['detail']
        assert client.get('/v1/reviews/event-a/questions').status_code == 410
        assert client.post('/v1/reviews/event-a/questions', json={'question': 'What is visible?', 'request_id': 'q-31'}).status_code == 410
    assert not INFERENCE_LOCK.locked()


def test_known_model_metadata_prefixes_are_removed_only_at_start():
    answer = video_qa.VideoAnswer.model_validate({
        'answer': '>>> No collision is visible in the clip.',
        'limitations': ['@unknown The camera cannot show the road beyond the bend.']})
    assert answer.limitations == ['The camera cannot show the road beyond the bend.']
    answer = video_qa.VideoAnswer.model_validate({
        'answer': 'No collision is visible in the clip.',
        'limitations': ['@uncertain The camera cannot show the road beyond the bend.']})
    assert answer.limitations == ['The camera cannot show the road beyond the bend.']
    from agent_pipeline.verification import clean_model_markup
    assert clean_model_markup('An @unknown marker appears in the image.') == 'An @unknown marker appears in the image.'
    assert clean_model_markup('>= 10 vehicles are visible.') == '>= 10 vehicles are visible.'

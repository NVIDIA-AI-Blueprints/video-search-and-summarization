# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Durability, backpressure and scheduling checks; no running model or service."""
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent_pipeline import video_qa
from agent_pipeline.clips import clip_path
from agent_pipeline.inference_scheduler import InferenceScheduler, InferenceCancelled
from agent_pipeline.store import Store


def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    assert predicate(), 'Timed out waiting for worker state'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('D1_CLIP_CACHE', str(tmp_path / 'clips'))
    store = Store(tmp_path / 'questions.sqlite')
    event = {'id': 'one', 'stream_id': 'recorded-one', 'clip_start': 1, 'clip_end': 21}
    with store.transaction() as db:
        db.execute('INSERT INTO jobs VALUES(?,?,?,0,?,NULL)', ('one', 0, 'complete', json.dumps(event)))
    clip_path('one').write_bytes(b'original-video')
    scheduler = InferenceScheduler()
    queue = video_qa.QuestionQueue(store, scheduler=scheduler)
    app = FastAPI()
    app.include_router(video_qa.router(store, queue=queue))
    with TestClient(app) as client:
        yield queue, store, client, event
    queue.stop()


def fake_answer(event, question, history, **kwargs):
    return {'answer': 'The visible vehicles form a queue.', 'limitations': ['The cause is outside view.'],
            'clip_sha256': kwargs['expected_sha256'], 'model': 'fake', 'answer_attempts': 1}


def submit(client, key='q1', question='Which lanes move?'):
    return client.post('/v1/reviews/one/questions', json={'request_id': key, 'question': question})


def test_held_background_keeps_question_queued_then_running_then_complete(setup, monkeypatch):
    queue, store, client, _ = setup
    entered, finish = threading.Event(), threading.Event()
    def answer(*args, **kwargs):
        entered.set()
        assert finish.wait(2)
        return fake_answer(*args, **kwargs)
    monkeypatch.setattr(video_qa, 'answer_video_question', answer)
    queue.scheduler.acquire()
    try:
        queue.start()
        assert submit(client).status_code == 202
        time.sleep(.04)
        pending = client.get('/v1/reviews/one/questions/q1').json()
        assert pending['status'] == 'queued'
        assert store.rows('SELECT attempts FROM event_question_jobs')[0]['attempts'] == 0
        assert not entered.is_set()
    finally:
        queue.scheduler.release()
    assert entered.wait(1)
    assert client.get('/v1/reviews/one/questions/q1').json()['status'] == 'running'
    finish.set()
    wait_until(lambda: queue.get('one', 'q1')['status'] == 'complete')
    complete = submit(client)
    assert complete.status_code == 200
    assert complete.json()['answer'] == 'The visible vehicles form a queue.'
    assert complete.json()['clip_sha256'] == hashlib.sha256(b'original-video').hexdigest()


def test_multiple_saved_questions_precede_waiting_background_slot(setup, monkeypatch):
    queue, _, client, _ = setup
    order = []
    def answer(event, question, history, **kwargs):
        order.append(question)
        return fake_answer(event, question, history, **kwargs)
    monkeypatch.setattr(video_qa, 'answer_video_question', answer)
    queue.scheduler.acquire()
    submit(client, 'q1', 'First question?')
    submit(client, 'q2', 'Second question?')
    def background():
        with queue.scheduler.slot(priority='background'):
            order.append('background')
    bg = threading.Thread(target=background)
    bg.start()
    queue.start()
    queue.scheduler.release()
    bg.join(2)
    assert not bg.is_alive()
    assert order == ['First question?', 'Second question?', 'background']


def test_idempotency_concurrent_admission_capacity_and_conflict(setup):
    queue, store, client, _ = setup
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: submit(client), range(8)))
    assert {r.status_code for r in responses} == {202}
    assert len({r.json()['id'] for r in responses}) == 1
    assert len(store.rows('SELECT * FROM event_question_jobs')) == 1
    assert submit(client, question='A different question?').status_code == 409
    for n in range(2, 9):
        assert submit(client, f'q{n}').status_code == 202
    full = submit(client, 'overflow')
    assert full.status_code == 429 and full.headers['retry-after'] == '5'
    assert 'full' in full.json()['detail']
    assert submit(client).status_code == 202  # Duplicate never consumes capacity.
    assert len(store.rows('SELECT * FROM event_question_jobs')) == 8


def test_restart_recovers_same_id_hash_and_history_without_duplicate_job(setup, monkeypatch):
    queue, store, client, event = setup
    submit(client)
    with store.transaction() as db:
        db.execute("UPDATE event_question_jobs SET status='running',attempts=1")
    queue.stop()
    observed = []
    def answer(saved, question, history, **kwargs):
        observed.append((saved, kwargs['expected_sha256']))
        return fake_answer(saved, question, history, **kwargs)
    monkeypatch.setattr(video_qa, 'answer_video_question', answer)
    restarted = video_qa.QuestionQueue(store, scheduler=queue.scheduler)
    try:
        restarted.start()
        wait_until(lambda: restarted.get('one', 'q1')['status'] == 'complete')
        assert observed == [(event, hashlib.sha256(b'original-video').hexdigest())]
        assert len(store.rows('SELECT * FROM event_question_jobs')) == 1
        assert store.rows('SELECT attempts FROM event_question_jobs')[0]['attempts'] == 2
        assert restarted.history('one')[0]['id'] == 'q1'
    finally:
        restarted.stop()


def test_repeated_interruptions_exhaust_bounded_recovery(setup, monkeypatch):
    queue, store, client, _ = setup
    submit(client)
    with store.transaction() as db:
        db.execute("UPDATE event_question_jobs SET status='running',attempts=3")
    monkeypatch.setattr(video_qa, 'answer_video_question', lambda *a, **k: pytest.fail('Exhausted job must not call model'))
    queue.start()
    body = queue.get('one', 'q1')
    assert body['status'] == 'failed' and body['error']['code'] == 'interrupted'
    assert queue.scheduler.acquire(blocking=False)
    queue.scheduler.release()


def test_clip_changed_after_admission_fails_without_upload(setup, monkeypatch):
    queue, _, client, _ = setup
    submit(client)
    clip_path('one').write_bytes(b'different-video')
    monkeypatch.setenv('COSMOS_BASE_URL', 'http://fake')
    monkeypatch.setenv('COSMOS_MODEL', 'fake')
    calls = []
    real_complete = video_qa.complete_video
    with httpx.Client(transport=httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(500))) as http:
        def complete(*args, **kwargs):
            kwargs["client"] = http
            return real_complete(*args, **kwargs)
        monkeypatch.setattr(video_qa, 'complete_video', complete)
        assert queue.run_once()
    body = queue.get('one', 'q1')
    assert body['status'] == 'failed' and body['error']['code'] == 'evidence_changed'
    assert 'answer' not in body and calls == []
    assert submit(client).status_code == 200  # Same failed request remains idempotent.


def test_missing_queued_clip_and_bad_answer_are_terminal_not_success(setup, monkeypatch):
    queue, _, client, _ = setup
    submit(client)
    clip_path('one').unlink()
    def missing(*args, **kwargs):
        clip_path('one').open('rb')
    monkeypatch.setattr(video_qa, 'answer_video_question', missing)
    assert queue.run_once()
    assert queue.get('one', 'q1')['error']['code'] == 'clip_unavailable'
    clip_path('one').write_bytes(b'original-video')
    submit(client, 'q2')
    def malformed(*args, **kwargs):
        raise ValueError('secret raw response should not leak')
    monkeypatch.setattr(video_qa, 'answer_video_question', malformed)
    assert queue.run_once()
    result = queue.get('one', 'q2')
    assert result['status'] == 'failed' and 'answer' not in result
    assert 'secret' not in json.dumps(result)


def test_stop_cancels_wait_without_consuming_attempt_and_recovers(setup, monkeypatch):
    queue, store, client, _ = setup
    monkeypatch.setattr(video_qa, 'answer_video_question', fake_answer)
    queue.scheduler.acquire()
    try:
        submit(client)
        queue.start()
        started = time.monotonic()
        assert queue.stop(timeout=.5)
        assert time.monotonic() - started < .7
        assert queue.get('one', 'q1')['status'] == 'queued'
        assert store.rows('SELECT attempts FROM event_question_jobs')[0]['attempts'] == 0
    finally:
        queue.scheduler.release()
    queue.start()
    wait_until(lambda: queue.get('one', 'q1')['status'] == 'complete')


def test_stop_has_bounded_join_for_inflight_http_and_no_second_worker(setup, monkeypatch):
    queue, _, client, _ = setup
    entered, finish = threading.Event(), threading.Event()
    def answer(*args, **kwargs):
        entered.set()
        assert finish.wait(2)
        return fake_answer(*args, **kwargs)
    monkeypatch.setattr(video_qa, 'answer_video_question', answer)
    submit(client)
    queue.start()
    assert entered.wait(1)
    started = time.monotonic()
    assert queue.stop(timeout=.02) is False
    assert time.monotonic() - started < .3
    with pytest.raises(RuntimeError, match='not finished'):
        queue.start()
    finish.set()
    wait_until(lambda: queue.get('one', 'q1')['status'] == 'complete')
    assert queue.stop(timeout=.5)


def test_terminal_retention_is_bounded_pending_and_legacy_are_preserved(setup, monkeypatch):
    queue, store, client, _ = setup
    queue.max_retained = 2
    monkeypatch.setattr(video_qa, 'answer_video_question', fake_answer)
    legacy = {'id': 'legacy', 'event_id': 'one', 'question': 'Earlier question?', 'answer': 'An earlier saved answer.', 'created_at': 0, 'clip_sha256': hashlib.sha256(b'original-video').hexdigest()}
    with store.transaction() as db:
        db.execute('INSERT INTO event_questions VALUES(?,?,?,?)', ('one', 'legacy', 0, json.dumps(legacy)))
    for n in range(4):
        submit(client, f'q{n}')
        queue.run_once()
    submit(client, 'pending')
    assert len(store.rows("SELECT * FROM event_question_jobs WHERE status='complete'")) == 2
    assert queue.get('one', 'pending')['status'] == 'queued'
    assert queue.get('one', 'legacy')['status'] == 'complete'
    assert json.loads(store.rows('SELECT body FROM event_questions')[0]['body']) == legacy
    with store.transaction() as db:
        db.execute("UPDATE event_question_jobs SET updated_at=0 WHERE status='complete'")
        queue._prune(db)
    assert len(store.rows('SELECT * FROM event_question_jobs')) == 1


def test_individual_status_rejects_conflicting_evidence(setup):
    queue, store, client, _ = setup
    submit(client)
    with store.transaction() as db:
        db.execute('INSERT INTO incident_candidates VALUES(?,?,?,?)', ('one', 'incident', 'confirmed', json.dumps({'clip_sha256': 'f' * 64})))
    response = client.get('/v1/reviews/one/questions/q1')
    assert response.status_code == 410


def test_scheduler_cancellation_releases_priority_waiter():
    scheduler, stop = InferenceScheduler(), threading.Event()
    scheduler.acquire()
    cancelled = []
    def interactive():
        try:
            with scheduler.slot(priority='interactive', stop=stop):
                pytest.fail('Cancelled waiter entered inference')
        except InferenceCancelled:
            cancelled.append(True)
    thread = threading.Thread(target=interactive)
    thread.start()
    stop.set()
    scheduler.wake()
    thread.join(1)
    scheduler.release()
    assert cancelled == [True]
    assert scheduler.acquire(blocking=False)
    scheduler.release()


def test_qa_correction_names_the_placeholder_field_and_never_saves_it(setup, monkeypatch):
    queue, _, client, _ = setup
    calls = []
    digest = hashlib.sha256(b'original-video').hexdigest()
    def complete(event, messages, **kwargs):
        calls.append(messages.copy())
        content = {'answer': 'A queue of vehicles is visible.', 'limitations': ['/no applicable uncertainty/'] if len(calls) == 1 else ['The camera does not show the road beyond the bend.']}
        return {'choices': [{'message': {'content': json.dumps(content)}, 'finish_reason': 'stop'}]}, digest, 'fake'
    monkeypatch.setattr(video_qa, 'complete_video', complete)
    submit(client)
    queue.run_once()
    body = queue.get('one', 'q1')
    assert body['status'] == 'complete' and body['answer_attempts'] == 2
    correction = calls[1][-1]['content']
    assert 'limitations[0]' in correction and 'observation_placeholder' in correction
    assert '/no applicable uncertainty/' not in body['answer']


def test_qa_length_finish_uses_one_correction_then_terminal_error(setup, monkeypatch):
    queue, _, client, _ = setup
    calls = []
    digest = hashlib.sha256(b'original-video').hexdigest()
    def truncated(event, messages, **kwargs):
        calls.append(messages.copy())
        raise video_qa.VideoCompletionTruncated({'choices': [{'message': {'content': '{"answer":"unfinished'}, 'finish_reason': 'length'}]}, digest, 'fake')
    monkeypatch.setattr(video_qa, 'complete_video', truncated)
    submit(client)
    queue.run_once()
    body = queue.get('one', 'q1')
    assert len(calls) == 2 and 'output_truncated' in calls[1][-1]['content']
    assert body['status'] == 'failed' and 'answer' not in body
    assert body['error']['validation_errors'][0]['code'] == 'output_truncated'
    assert not queue.scheduler.locked()

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import base64
import io
import json
import time
from functools import lru_cache

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from agent_pipeline.capture import Capture
from agent_pipeline.contracts import StreamConfig
from agent_pipeline.ingest import Runtime, create_app, prepare_input, response_provenance
from agent_pipeline.store import Store


@lru_cache(maxsize=100)
def jpeg(color='red'):
    buffer = io.BytesIO()
    Image.new('RGB', (960, 540), color).save(buffer, format='JPEG')
    return buffer.getvalue()


def captured_window(end=108.0, omitted=()):
    cap = Capture('rtsp://camera/live')
    for index in range(81):
        if index not in omitted:
            cap.accept_frame(jpeg((70 + index, 20, 40)), end - 8 + index / 10)
    return cap


def config(**overrides):
    return StreamConfig(rtsp_url='rtsp://camera/live',
                        question={'instructions': 'Is traffic stopped or crawling?'}, **overrides)


def test_video_window_uses_distinct_real_frames_and_actual_time_span():
    cap = captured_window()
    window, reason = cap.sample_window(8, 1, now=108.1)
    assert reason is None
    assert window['times'] == pytest.approx([101 + n for n in range(8)])
    assert len(set(window['sequences'])) == 8
    assert len(set(window['frames'])) == 8
    assert window['fps'] == pytest.approx(1)


@pytest.mark.parametrize('case', ['warming', 'stale', 'gap'])
def test_video_window_rejects_incomplete_stale_or_discontinuous_capture(case):
    cap = captured_window(omitted=range(20, 46) if case == 'gap' else ())
    if case == 'warming':
        cap = Capture('rtsp://camera/live')
        cap.accept_frame(jpeg(), 108)
    window, reason = cap.sample_window(8, 1, now=114 if case == 'stale' else 108.1)
    assert window is None
    assert reason


def test_identical_jpegs_are_not_fresh_video_even_with_new_sequences():
    cap = Capture('rtsp://camera/live')
    for index in range(81):
        cap.accept_frame(jpeg(), 100 + index / 10)
    window, reason = cap.sample_window(8, 1, now=108.1)
    assert window is None
    assert 'unchanged' in reason


def test_capture_history_is_bounded_by_frame_count_and_bytes():
    cap = Capture('rtsp://camera/live')
    for index in range(400):
        cap.accept_frame(b'frame', index / 10)
    assert len(cap.history) == 320
    assert cap.history_bytes == 320 * 5
    data = b'x' * (1024 * 1024)
    for index in range(40):
        cap.accept_frame(data, 40 + index)
    assert cap.history_bytes <= 32 * 1024 * 1024
    assert len(cap.history) == 32


def test_video_request_preserves_frame_timing_and_bounds_uploaded_resolution():
    prepared, reason = prepare_input(captured_window(), config(input_mode='video').model_dump(), 108.1, 1000.1)
    assert reason is None
    body = prepared['body']
    assert 'image_base64' not in body
    assert len(body['video']['frames_base64']) == 8
    assert body['video']['fps'] == pytest.approx(1)
    with Image.open(io.BytesIO(base64.b64decode(body['video']['frames_base64'][0]))) as frame:
        assert frame.size == (448, 252)
    provenance = prepared['input']
    assert provenance['window_start'] == pytest.approx(993)
    assert provenance['window_end'] == pytest.approx(1000)
    assert provenance['frame_timestamps'] == pytest.approx(list(range(993, 1001)))
    assert provenance['timestamp_source'] == 'host_frame_arrival'
    assert prepared['captured_at'] == provenance['window_end']


def test_existing_image_profiles_keep_original_input():
    cap = captured_window()
    prepared, reason = prepare_input(cap, {'input_mode': 'image'}, 108.1, 1000.1)
    assert reason is None
    assert 'video' not in prepared['body']
    assert base64.b64decode(prepared['body']['image_base64']) == cap.frame
    assert prepared['input']['mode'] == 'image'
    assert prepared['input']['frame_count'] == 1


def test_mode_and_sampling_changes_are_semantic_but_default_migration_is_not(tmp_path):
    store = Store(tmp_path / 'ingest.sqlite')
    legacy = {'rtsp_url': 'rtsp://camera/live', 'question': config().question.model_dump(),
              'vios_sensor': None, 'enabled': True, 'interval_seconds': 2}
    assert store.put_config('stream', 'traffic', legacy) == 1
    with TestClient(create_app(store, start_workers=False)) as client:
        assert client.put('/v1/streams/traffic', json=config().model_dump()).json()['revision'] == 1
        assert client.patch('/v1/streams/traffic', json={'input_mode': 'video'}).json()['revision'] == 2
        assert client.patch('/v1/streams/traffic', json={'interval_seconds': 3, 'enabled': False}).json()['revision'] == 2
        assert client.patch('/v1/streams/traffic', json={'video_frames': 12}).json()['revision'] == 3
        assert client.patch('/v1/streams/traffic', json={'video_fps': 2}).json()['revision'] == 4
        for invalid in ({'video_frames': 1}, {'video_frames': 17}, {'video_fps': 0}, {'video_fps': 6}):
            assert client.patch('/v1/streams/traffic', json=invalid).status_code == 422


def test_provenance_uses_live_response_and_legacy_health_not_checkout_lock():
    calls = []
    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={'model': {'model_id': 'LiquidAI/d1-3B', 'revision': 'loaded-old'}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert response_provenance({'model': {'model_id': 'LiquidAI/d1-2B', 'revision': 'loaded-new'}}, client, 'http://model') == ('LiquidAI/d1-2B', 'loaded-new')
        assert calls == []
        assert response_provenance({}, client, 'http://model') == ('LiquidAI/d1-3B', 'loaded-old')
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'ready': True}))) as client:
        with pytest.raises(ValueError, match='provenance'):
            response_provenance({}, client, 'http://model')


def test_runtime_publishes_video_decision_with_actual_loaded_model(tmp_path, monkeypatch):
    from agent_pipeline import ingest
    store = Store(tmp_path / 'ingest.sqlite')
    store.put_config('stream', 'traffic', config(input_mode='video').model_dump())
    cap = captured_window(end=time.monotonic())
    cap.capture = lambda: None
    runtime = Runtime(store, capture_factory=lambda *args: cap)
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        runtime.stop.set()
        return httpx.Response(200, json={'result': {'answers': {'scene': {'noul': .93}}},
            'model': {'model_id': 'LiquidAI/d1-2B', 'revision': 'actual-revision'}, 'processing_ms': 220})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(ingest.httpx, 'Client', lambda **kwargs: client)
    runtime.run()
    assert len(requests) == 1
    assert requests[0]['video']['fps'] == pytest.approx(1)
    row = store.rows('SELECT * FROM outbox')[0]
    decision = json.loads(row['body'])
    assert decision['model'] == 'LiquidAI/d1-2B'
    assert decision['model_revision'] == 'actual-revision'
    assert decision['input']['mode'] == 'video'
    assert decision['input']['frame_count'] == 8
    assert decision['config_revision'] == 1
    assert decision['answer']['noul'] == .93


def test_video_readiness_reports_buffering_instead_of_ready(tmp_path, monkeypatch):
    from agent_pipeline import ingest
    store = Store(tmp_path / 'ingest.sqlite')
    store.put_config('stream', 'traffic', config(input_mode='video').model_dump())
    runtime = Runtime(store)
    cap = Capture('rtsp://camera/live')
    cap.accept_frame(jpeg())
    runtime.captures['traffic'] = cap
    monkeypatch.setattr(ingest, 'Runtime', lambda _: runtime)
    with TestClient(create_app(store, start_workers=False)) as client:
        response = client.get('/v1/health/ready')
        assert response.status_code == 503
        state = response.json()['streams']['traffic']
        assert state['camera'] == 'streaming'
        assert state['input_ready'] is False
        assert 'Collecting' in state['input_status']
        runtime.captures['traffic'] = captured_window(end=time.monotonic())
        assert client.get('/v1/health/ready').status_code == 200

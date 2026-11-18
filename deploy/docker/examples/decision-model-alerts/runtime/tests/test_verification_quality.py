# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Offline regressions for observed raw-output failures, not an accuracy benchmark."""
import hashlib
import json
import threading
from contextlib import contextmanager

import httpx
import pytest

from agent_pipeline import review
from agent_pipeline.clips import clip_path
from agent_pipeline.verification import (
    VERIFICATION_POLICY_VERSION, VERIFICATION_SYSTEM, Verification,
    VerificationFormatError, parse_verification, verification_policy_sha256,
)


def assessment(verdict='confirmed', **changes):
    return {'verdict': verdict, 'summary': 'The right-side queue is moving slowly.',
            'evidence': ['Closely spaced vehicles make little forward progress.'],
            'uncertainty': [], **changes}


@pytest.mark.parametrize('verdict', ['confirmed', 'rejected'])
def test_no_extra_uncertainty_is_explicitly_valid(verdict):
    body = assessment(verdict)
    assert parse_verification(json.dumps(body)) == body


def test_uncertain_needs_real_limitation_and_reports_its_field():
    with pytest.raises(VerificationFormatError) as caught:
        parse_verification(json.dumps(assessment('uncertain')))
    assert caught.value.validation_errors[0]['field'] == 'uncertainty'
    assert caught.value.validation_errors[0]['code'] == 'uncertainty_required'
    body = assessment('uncertain', uncertainty=['The short clip does not show enough motion.'])
    assert parse_verification(json.dumps(body)) == body


@pytest.mark.parametrize('field,index,bad,code', [
    ('uncertainty', 0, '/no applicable uncertainty/', 'observation_placeholder'),
    ('uncertainty', 0, '/no further details/', 'observation_placeholder'),
    ('uncertainty', 0, '/no additional details/', 'observation_placeholder'),
    ('evidence', 0, '/no applicable uncertainty/', 'observation_placeholder'),
    ('summary', None, '/no further details/', 'observation_placeholder'),
    ('evidence', 0, 'The vehicles move at a蜗牛', 'observation_mixed_script'),
    ('evidence', 0, 'The cars are inching前进.', 'observation_mixed_script'),
    ('evidence', 0, 'The cars are stationary throughout the', 'observation_unfinished'),
    ('evidence', 0, 'The cars are stationary throughout the 20.', 'observation_unfinished'),
    ('evidence', 0, 'The cars are stationary throughout the 21.', 'observation_unfinished'),
])
def test_observed_defects_are_rejected_without_substituting_a_claim(field, index, bad, code):
    body = assessment(**{field: bad if index is None else [bad]})
    with pytest.raises(VerificationFormatError) as caught:
        parse_verification(json.dumps(body))
    errors = caught.value.validation_errors
    assert errors[0]['field'] == field + ('' if index is None else f'[{index}]')
    assert errors[0]['code'] == code
    assert bad not in str(caught.value)
    assert all('input' not in error for error in errors)


def test_validation_preserves_meaningful_text_and_is_not_a_grounding_filter():
    # Format acceptance does not establish any of these visual claims. In
    # particular, unsupported emergency identities need actual media evaluation.
    body = assessment(
        summary='>>> The right-side queue is moving slowly.',
        evidence=['>>> More than 80% of cars are stopped near marked police vehicles.',
                  'The café sign remains visible throughout the 20-second clip.'])
    result = parse_verification(json.dumps(body))
    assert result['summary'] == body['summary'][4:]
    assert result['evidence'][0] == body['evidence'][0][4:]
    assert result['evidence'][1] == body['evidence'][1]


def test_complete_observations_have_headroom_but_are_bounded():
    observation = ('The closely spaced vehicles in the queue on the right move slowly across the clip, '
                   'while traffic farther left has visibly wider spacing and advances more freely; '
                   'the camera does not show what happens beyond the bend.')
    assert 180 < len(observation) < 400
    assert parse_verification(json.dumps(assessment(evidence=[observation])))['evidence'] == [observation]
    with pytest.raises(VerificationFormatError) as caught:
        parse_verification(json.dumps(assessment(evidence=[observation * 3])))
    assert caught.value.validation_errors[0]['field'] == 'evidence[0]'
    assert caught.value.validation_errors[0]['code'] == 'string_too_long'


@pytest.mark.parametrize('bad', [']}' + json.dumps(assessment()), 'Not JSON', '', None,
                                json.dumps({**assessment(), 'verdict': 'yes'}),
                                json.dumps({**assessment(), 'evidence': []})])
def test_no_fabricated_default_verdict_for_invalid_content(bad):
    with pytest.raises(VerificationFormatError) as caught:
        parse_verification(bad)
    assert caught.value.validation_errors


@pytest.fixture
def event(monkeypatch, tmp_path):
    monkeypatch.setenv('D1_CLIP_CACHE', str(tmp_path / 'clips'))
    monkeypatch.setenv('COSMOS_BASE_URL', 'http://cosmos')
    monkeypatch.setenv('COSMOS_MODEL', 'test-cosmos')
    return {'id': 'quality-event', 'stream_id': 'traffic', 'captured_at': 1000,
            'decision_ids': ['decision'], 'clip_start': 985, 'clip_end': 1005,
            'trigger': {'question': {'type': 'noul', 'instructions': 'Is traffic queued?'},
                        'answer': {'noul': .99}, 'vios_sensor': 'traffic'},
            'rule': {'field': 'noul', 'operator': 'gte', 'value': .85,
                     'review_prompt': 'Explain the visible queue.'}}


def call_reviews(event, responses, calls, callback=None, stop=None):
    pending = iter(responses)
    def handle(request):
        calls.append(request)
        if request.method == 'GET':
            return httpx.Response(200, content=b'exact-quality-clip')
        if request.method == 'DELETE':
            return httpx.Response(200)
        if request.url.path == '/v1/files':
            assert b'exact-quality-clip' in request.content
            return httpx.Response(200, json={'id': 'uploaded'})
        content, finish = next(pending)
        if callback:
            callback()
        return httpx.Response(200, json={'choices': [{'message': {'content': content},
                                                      'finish_reason': finish}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        return review.review_event(event, client, lambda _: {'media_url': 'http://vios/clip'}, stop=stop)


def test_corrective_generation_names_field_and_retains_exact_video(event, monkeypatch):
    state = {'active': False, 'slots': 0}
    class Scheduler:
        @contextmanager
        def slot(self, priority, stop=None):
            assert priority == 'background'
            assert not state['active']
            state.update(active=True, slots=state['slots'] + 1)
            try:
                yield
            finally:
                state['active'] = False
    original = review.parse_verification
    def parse_after_releasing_slot(content):
        assert not state['active']
        return original(content)
    monkeypatch.setattr(review, 'INFERENCE_SCHEDULER', Scheduler())
    monkeypatch.setattr(review, 'parse_verification', parse_after_releasing_slot)
    invalid = assessment(uncertainty=['/no applicable uncertainty/'])
    calls = []
    result = call_reviews(event, [(json.dumps(invalid), 'stop'),
                                 (json.dumps(assessment('rejected')), 'stop')], calls)
    assert result['verdict'] == 'rejected'
    assert result['uncertainty'] == []
    assert result['verification_attempts'] == state['slots'] == 2
    assert result['verification_policy_version'] == VERIFICATION_POLICY_VERSION
    assert result['verification_policy_sha256'] == verification_policy_sha256()
    assert result['clip_sha256'] == hashlib.sha256(b'exact-quality-clip').hexdigest()
    assert result['verification_format_failures'][0]['validation_errors'][0]['field'] == 'uncertainty[0]'
    completions = [json.loads(r.content) for r in calls if r.url.path == '/v1/chat/completions']
    correction = completions[1]['messages'][-1]['content']
    assert 'uncertainty[0]' in correction and 'observation_placeholder' in correction
    assert 'uncertainty may be []' in correction
    assert sum(r.method == 'GET' for r in calls) == 1
    assert sum(r.method == 'DELETE' for r in calls) == 2


@pytest.mark.parametrize('finish,content,expected_code', [
    ('stop', json.dumps(assessment(uncertainty=['/no applicable uncertainty/'])), 'observation_placeholder'),
    ('length', json.dumps(assessment()), 'output_limit'),
])
def test_two_failed_generations_are_terminal_with_safe_diagnostics(event, finish, content, expected_code):
    calls = []
    with pytest.raises(VerificationFormatError) as caught:
        call_reviews(event, [(content, finish), (content, finish)], calls)
    error = caught.value
    assert error.diagnostics['generation_attempts'] == 2
    assert len(error.diagnostics['attempt_failures']) == 2
    assert error.validation_errors[0]['code'] == expected_code
    assert error.diagnostics['clip_sha256'] == hashlib.sha256(b'exact-quality-clip').hexdigest()
    assert error.last_response['choices'][0]['message']['content'] == content
    for failure in error.diagnostics['attempt_failures']:
        assert failure['finish_reason'] == finish
        assert failure['content_length'] == len(content)
        assert len(failure['response_sha256']) == 64
        assert 'content' not in failure and 'response' not in failure
    assert sum(r.url.path == '/v1/chat/completions' for r in calls) == 2
    assert sum(r.method == 'DELETE' for r in calls) == 2
    assert clip_path(event['id']).read_bytes() == b'exact-quality-clip'


def test_output_limit_gets_only_one_correction_even_if_first_json_was_parseable(event):
    calls = []
    result = call_reviews(event, [(json.dumps(assessment()), 'length'),
                                 (json.dumps(assessment()), 'stop')], calls)
    assert result['verification_attempts'] == 2
    assert result['verification_format_failures'][0]['validation_errors'][0]['code'] == 'output_limit'


def test_replacement_video_is_rejected_before_corrective_upload(event):
    calls = []
    def replace_video():
        clip_path(event['id']).write_bytes(b'different-video')
    with pytest.raises(review.ClipEvidenceChanged):
        call_reviews(event, [(json.dumps(assessment(uncertainty=['/no further details/'])), 'stop')],
                     calls, callback=replace_video)
    assert sum(r.url.path == '/v1/chat/completions' for r in calls) == 1
    assert sum(r.url.path == '/v1/files' and r.method == 'POST' for r in calls) == 1
    assert sum(r.method == 'DELETE' for r in calls) == 1


def test_policy_provenance_has_stable_hash_and_no_confidence_hint():
    assert len(verification_policy_sha256()) == 64
    assert verification_policy_sha256() == verification_policy_sha256()
    assert Verification.model_json_schema()['properties']['evidence']['items']['maxLength'] == 400
    assert 'preferably 10-25 words' in VERIFICATION_SYSTEM
    assert 'Assess directions separately' in VERIFICATION_SYSTEM
    assert 'do not track vehicles' in VERIFICATION_SYSTEM
    assert Verification.model_json_schema()['properties']['evidence']['maxItems'] == 2


def test_third_observation_requires_correction_not_silent_truncation():
    body = assessment(evidence=['Traffic on the right moves slowly.',
                                'Traffic farther left moves freely.',
                                'A third observation remains in the raw response.'])
    with pytest.raises(VerificationFormatError) as caught:
        parse_verification(json.dumps(body))
    assert caught.value.validation_errors[0]['field'] == 'evidence'
    assert caught.value.validation_errors[0]['code'] == 'too_long'
    assert len(body['evidence']) == 3


def test_shutdown_before_review_does_not_fetch_or_generate(event):
    stop = threading.Event(); stop.set()
    calls = []
    with pytest.raises(review.InferenceCancelled):
        call_reviews(event, [], calls, stop=stop)
    assert calls == []
    assert not clip_path(event['id']).exists()


def test_shutdown_prevents_corrective_generation_after_first_response(event):
    stop = threading.Event(); calls = []
    with pytest.raises(review.InferenceCancelled):
        call_reviews(event, [(json.dumps(assessment(uncertainty=['/no additional details/'])), 'stop')],
                     calls, callback=stop.set, stop=stop)
    assert sum(r.url.path == '/v1/chat/completions' for r in calls) == 1
    assert sum(r.method == 'DELETE' for r in calls) == 1


def test_shutdown_cancels_a_queued_background_review(event, monkeypatch):
    from agent_pipeline.inference_scheduler import InferenceScheduler
    scheduler = InferenceScheduler(); scheduler.reserve('operator-question')
    entered = threading.Event()
    class ObservableScheduler:
        @contextmanager
        def slot(self, **kwargs):
            entered.set()
            with scheduler.slot(**kwargs):
                yield
    monkeypatch.setattr(review, 'INFERENCE_SCHEDULER', ObservableScheduler())
    clip_path(event['id']).write_bytes(b'exact-quality-clip')
    stop = threading.Event(); calls = []; failures = []
    def run():
        try:
            call_reviews(event, [], calls, stop=stop)
        except BaseException as exc:
            failures.append(exc)
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert entered.wait(1)
        stop.set(); scheduler.wake()
        worker.join(1)
        assert not worker.is_alive()
        assert len(failures) == 1 and isinstance(failures[0], review.InferenceCancelled)
        assert calls == []
    finally:
        stop.set(); scheduler.unreserve('operator-question'); scheduler.wake(); worker.join(1)

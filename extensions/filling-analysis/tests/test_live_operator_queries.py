# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
from uuid import uuid4

from test_live_api import setup


def record(routes, session, cycle, seq, **updates):
    event = copy.deepcopy(routes['events']['events'][0])
    event.update(event_id=f'event-{seq}', seq=seq, track_id=f'{session}:epoch-1:cycle-{cycle}', **updates)
    return event


def query(client, session, question='inspect', **kwargs):
    return client.post('/api/live/query', json={'session_id': session, 'question': question, **kwargs})


def test_cycle_identity_is_not_global_event_sequence(setup):
    client, routes, _, _, _, session = setup
    routes['events']['events'] = [record(routes, session, 22, 305),
        record(routes, session, 305, 588, status='normal', final_level=.72803)]
    data = query(client, session, 'What happened with bottle cycle-305?').json()
    assert data['query_status'] == 'ok'
    assert [e['seq'] for e in data['matches']] == [588]
    assert 'normal' in data['answer'] and '72.8%' in data['answer']


def test_typo_comparison_and_underfill_shortfall(setup):
    client, routes, _, _, _, session = setup
    routes['events']['events'].append(record(routes, session, 305, 588, status='normal', final_level=.72803))
    data = query(client, session, 'Compare bottle cyce-297 with cycle-305').json()
    assert len(data['matches']) == 2
    assert data['requested_cycle_ids'] == ['cycle-297', 'cycle-305']
    assert '44.9%' in data['answer'] and '67.8%' in data['answer']
    assert '22.9 percentage points' in data['answer']


def test_unknown_cycle_does_not_return_unrelated_record(setup):
    client, _, _, requests, _, session = setup
    data = query(client, session, 'What happened with cycle-99999').json()
    assert data['query_status'] == 'not_found' and data['matches'] == []
    assert all(r.url.path.startswith('/live/') for r in requests)


def test_full_identity_cross_session_and_epoch_rejected(setup):
    client, _, _, _, _, session = setup
    assert query(client, session, cycle_id=f'{uuid4()}:epoch-1:cycle-297').status_code == 409
    assert query(client, session, cycle_id=f'{session}:epoch-1:cycle-297', epoch=2).status_code == 409
    assert query(client, session, cycle_id=f'{session}:epoch-0:cycle-297').status_code == 422


def test_reused_label_requires_epoch_and_keeps_historical_provenance(setup):
    client, routes, _, _, _, session = setup
    old = routes['events']['events'][0]
    old['measurement']['pipeline_sha256'] = 'old'
    routes['status']['epoch'] = 2
    routes['status']['measurement'] = {'pipeline_sha256': 'new'}
    history = [{'epoch': 1, 'measurement': old['measurement'], 'clock': {'verified': False}}]
    routes['status']['measurement_history'] = history
    routes['events']['events'].append(record(routes, session, 297, 2, epoch=2))
    routes['events']['events'][1]['track_id'] = f'{session}:epoch-2:cycle-297'
    ambiguous = query(client, session, 'cycle-297').json()
    assert ambiguous['query_status'] == 'ambiguous' and ambiguous['matches'] == []
    assert len(ambiguous['candidates']) == 2
    selected = query(client, session, 'cycle-297', epoch=1).json()
    assert selected['matches'][0]['measurement']['pipeline_sha256'] == 'old'
    assert selected['measurement_history'] == history


def test_in_progress_keeps_provisional_separate_from_final_events(setup):
    client, routes, _, _, _, session = setup
    routes['status']['current'] = {'track_id': f'{session}:epoch-1:cycle-298',
        'phase': 'overflow_observed', 'level': None,
        'quality': {'readable': False, 'reason': 'Exterior overflow obscures the liquid boundary.'}}
    for prompt in ('cycle-298', 'What is happening to the current bottle?'):
        data = query(client, session, prompt).json()
        assert data['query_status'] == 'in_progress' and not data['matches']
        assert data['epoch'] == 1
        assert 'overflow' in data['answer'] and '0.0%' not in data['answer']


def test_latest_and_worst_are_bounded(setup):
    client, routes, _, _, _, session = setup
    routes['events']['events'].extend([
        record(routes, session, 298, 2, status='overflow'),
        record(routes, session, 299, 3, status='overflow'),
        record(routes, session, 300, 4, final_level=.2)])
    latest = query(client, session, 'Show latest overflow').json()
    assert [e['seq'] for e in latest['matches']] == [3]
    latest_completed = query(client, session, 'Show the latest completed overflow and its available evidence.').json()
    assert [e['seq'] for e in latest_completed['matches']] == [3]
    latest_two = query(client, session, 'Show the last 2 overflows').json()
    assert [e['seq'] for e in latest_two['matches']] == [3, 2]
    worst = query(client, session, 'Which underfill had the largest shortfall?').json()
    assert [e['seq'] for e in worst['matches']] == [4]
    limited = query(client, session, 'List anomalies', limit=2).json()
    assert len(limited['matches']) == 2 and limited['total_matches'] == 4 and limited['truncated']
    assert query(client, session, limit=51).status_code == 422


def snapshot_fixture(routes, stream, session):
    event = routes['events']['events'][0]
    event['snapshot_evidences'] = [{'session_id': session, 'stream_id': stream,
        'event_id': event['event_id'], 'frame_id': 94, 'source_pts_seconds': 3.7,
        'verified': True, 'verification': 'exact-decoded-frame', 'mime_type': 'image/jpeg',
        'private_path': '/state/private.jpg'}]
    routes['evidence'] = b'\xff\xd8test-image'
    return event


def test_snapshot_has_exact_source_binding_and_no_clip_clock_assumption(setup):
    client, routes, _, requests, stream, session = setup
    event = snapshot_fixture(routes, stream, session)
    data = query(client, session, 'cycle-297').json()
    image = data['snapshot_evidences'][0]
    assert not data['clock']['verified'] and data['video_evidences'] == []
    assert image['public_url'].startswith('http://vss.test/filling/api/live/evidence?')
    assert 'private_path' not in image
    result = client.get('/api/live/evidence', params={'session_id': session, 'event_id': event['event_id'], 'frame_id': 94})
    assert result.status_code == 200 and result.content == routes['evidence']
    assert requests[-1].url.host == 'worker'
    assert client.get('/api/live/evidence', params={'session_id': session, 'event_id': event['event_id'], 'frame_id': 95}).status_code == 404


def test_snapshot_forged_identity_is_rejected(setup):
    client, routes, _, _, stream, session = setup
    event = snapshot_fixture(routes, stream, session)
    event['snapshot_evidences'][0]['event_id'] = 'other'
    assert query(client, session, 'cycle-297').status_code == 503


def test_non_jpeg_worker_payload_is_rejected(setup):
    client, routes, _, _, stream, session = setup
    event = snapshot_fixture(routes, stream, session)
    routes['evidence'] = b'<html>not an image</html>'
    assert client.get('/api/live/evidence', params={'session_id': session, 'event_id': event['event_id'], 'frame_id': 94}).status_code == 503


def test_latest_respects_explicit_historical_epoch(setup):
    client, routes, _, _, _, session = setup
    old = record(routes, session, 298, 2, status='overflow')
    new = record(routes, session, 405, 3, status='overflow', epoch=2)
    new['track_id'] = f'{session}:epoch-2:cycle-405'
    routes['events']['events'] = [old, new]
    data = query(client, session, 'Show the latest overflow', epoch=1).json()
    assert [e['track_id'] for e in data['matches']] == [old['track_id']]


def test_stopped_session_never_claims_persisted_current_is_active(setup):
    client, routes, _, _, _, session = setup
    routes['status'].update(status='stopped', current={'track_id': 'cycle-298', 'level': .5, 'phase': 'filling'})
    data = query(client, session, 'What is the current bottle right now?').json()
    assert data['query_status'] == 'ok' and data['current'] == {}
    assert data['session_status'] == 'stopped' and '50.0%' not in data['answer']
    assert query(client, session, 'What happened with cycle-298?').json()['query_status'] == 'not_found'


def test_truncated_history_never_claims_latest_from_old_prefix(setup, monkeypatch):
    from backend.live import LiveProxy
    client, routes, _, _, _, session = setup
    async def incomplete(self, sid, status):
        return [dict(routes['events']['events'][0], status='overflow')], True
    monkeypatch.setattr(LiveProxy, 'all_events', incomplete)
    data = query(client, session, 'Show the latest overflow').json()
    assert data['query_status'] == 'unsupported' and data['truncated'] and data['matches'] == []


def test_current_cannot_be_substituted_across_explicit_epochs(setup):
    client, routes, _, _, _, session = setup
    routes['status'].update(epoch=2, current={'track_id': 'cycle-405', 'level': .5, 'phase': 'filling'})
    data = query(client, session, 'What is the current bottle?', epoch=1).json()
    assert data['query_status'] == 'ok' and data['current'] == {}
    assert '50.0%' not in data['answer'] and 'Requested epoch 1' in data['answer']

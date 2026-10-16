# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows.capture import atomic_json, digest, seal
from flows.pas_ground_truth import frame_supported_queries, supports
from replay_campaign import replay


def fixture():
    tracks = [{'track_uid': uid, 'chunk_id': clip, 'eligible_for_gt': True,
               'representative_frame': {'frame_attribute': {'shoe color': color}}}
              for uid, clip, color in [('t1', 'a', 'white'), ('t2', 'b', 'not visible')]]
    q = {'query_id': 'pas1', 'query_domain': 'pas', 'query': 'white shoes',
         'resolved_attributes': {'shoe_color': 'white'},
         'relevant_track_uids': ['t1', 't2'], 'relevant_clip_ids': ['a', 'b'],
         'segments': []}
    empty = {**q, 'query_id': 'pas2', 'relevant_track_uids': ['t2']}
    event = {'query_id': 'event1', 'query_domain': 'event', 'query': 'event',
             'relevant_clip_ids': ['b']}
    return [q, empty, event], tracks


def test_frame_support_excludes_and_preserves_input():
    queries, tracks = fixture()
    original = json.dumps(queries)
    kept, excluded = frame_supported_queries(queries, tracks, {'a', 'b'})
    assert kept[0]['relevant_clip_ids'] == ['a']
    assert 'segments' not in kept[0]
    assert kept[1] == queries[2]
    assert excluded == [{'query_id': 'pas2', 'query_domain': 'pas', 'reason': 'no_frame_supported_positives'}]
    assert json.dumps(queries) == original


def test_all_values_directional_matching_and_unknowns():
    assert supports({'accessories[2]': ['Hat', ' BAG ']}, {'accessories': ['black hat', 'white bag']})
    assert not supports({'accessories': ['hat', 'bag']}, {'accessories': ['hat']})
    assert not supports({'shoe_color': 'white shoes'}, {'shoe color': 'white'})
    assert not supports({'shoe_color': 'unknown'}, {'shoe color': 'unknown'})
    with pytest.raises(KeyError): supports({'shoe_color': 'white'}, {})


def test_invalid_tracks_fail():
    queries, tracks = fixture()
    with pytest.raises(ValueError): frame_supported_queries(queries, tracks + tracks[:1], {'a', 'b'})
    tracks[0]['eligible_for_gt'] = False
    with pytest.raises(ValueError): frame_supported_queries(queries, tracks, {'a', 'b'})


def test_replay_cohort_modes_and_immutable_evidence(tmp_path, monkeypatch):
    queries, tracks = fixture()
    campaign = tmp_path / 'campaign'
    snapshot = campaign / 'snapshot'
    mapping = {c: {'sensor_id': c, 'filename': c + '.mp4'} for c in 'ab'}
    atomic_json(snapshot / 'queries.json', queries)
    atomic_json(snapshot / 'gallery-mapping.json', mapping)
    atomic_json(snapshot / 'dataset/additional_gt/tracks_pas_attributes.json', {'tracks': tracks})
    seal(snapshot, [str(p.relative_to(snapshot)) for p in snapshot.rglob('*.json')])
    atomic_json(campaign / 'campaign.json', {'campaign_id': 'test', 'inputs': {'settings': {'modes': ['embed', 'attribute']}}})
    atomic_json(campaign / 'identity.json', {'sha256': digest(campaign / 'campaign.json')})
    for q in queries:
        attempt = campaign / 'attempts/embed' / q['query_id'] / 'a'
        atomic_json(attempt / 'request.json', {'plan': {'path': 'embed', 'top_k': 10}})
        atomic_json(attempt / 'process.json', {'elapsed_s': 1})
        atomic_json(attempt / 'response.json', {'envelope': {'data': [{'clip_id': 'a'}]}})
        seal(attempt, ['request.json', 'process.json', 'response.json'])
    before = {str(p): digest(p) for p in campaign.rglob('*') if p.is_file()}
    def forbidden(*args, **kwargs): raise AssertionError('Offline replay attempted inference/network')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    r = replay(campaign, tmp_path / 'report', pas_frame_supported=True, modes=['embed'])
    assert set(r['modes']) == {'embed'}
    assert r['cohort']['eligible'] == {'pas': 1, 'event': 1}
    groups = r['modes']['embed']['groups']
    assert groups['pas']['raw']['mAP@10'] == 1
    assert groups['event']['raw']['mAP@10'] == 0
    assert groups['all']['raw']['mAP@10'] == .5
    assert groups['all']['partial'] is False
    assert not r['modes']['embed']['unanswered']
    assert before == {str(p): digest(p) for p in campaign.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        replay(campaign, tmp_path / 'invalid', scorer='native', pas_frame_supported=True)
    with pytest.raises(ValueError):
        replay(campaign, tmp_path / 'invalid', modes=['fusion'])

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_subset import prepare
from comparison_common import load_inputs, validate_vectors, write_bundle, load_bundle, Network


def dataset(tmp_path):
    (tmp_path / 'clips').mkdir()
    clips = []
    for name in ['b', 'a', 'c']:
        (tmp_path / 'clips' / f'{name}.mp4').write_bytes(name.encode())
        clips.append({'chunk_id': name, 'path': f'clips/{name}.mp4'})
    (tmp_path / 'manifest.json').write_text(json.dumps({'clips': clips}))
    (tmp_path / 'gt').mkdir()
    queries = [
        {'query': ' exact text  ', 'query_domain': 'event', 'relevant_clip_ids': ['a', 'c'], 'near_universal': False},
        {'query': 'excluded', 'query_domain': 'pas', 'relevant_clip_ids': ['b']},
        {'query': 'broad', 'query_domain': 'event', 'relevant_clip_ids': ['a','b'], 'near_universal': True},
    ]
    (tmp_path / 'gt/queries_gt.json').write_text(json.dumps({'queries': queries, 'meta': {}}))
    (tmp_path / 'list.txt').write_text('a.mp4\nb.mp4\n')
    return tmp_path


def test_order_labels_text_and_fingerprint(tmp_path):
    root = dataset(tmp_path)
    prepare(root, root / 'list.txt', root / 'out')
    subset, selection = load_inputs(root / 'out/subset.json', root / 'out/selection.json')
    assert selection['video_ids'] == ['b','a']
    assert selection['text_ids'] == ['row:0','row:2']
    assert subset['queries'][0]['query'] == ' exact text  '
    assert subset['queries'][0]['relevant_clip_ids'] == ['a']
    assert subset['queries'][1]['near_universal'] is True
    assert selection['queries'][0]['original_relevant_clip_ids'] == ['a','c']
    subset['queries'][0]['query'] = 'tampered'
    (root / 'out/subset.json').write_text(json.dumps(subset))
    with pytest.raises(ValueError, match='fingerprint'):
        load_inputs(root / 'out/subset.json', root / 'out/selection.json')


@pytest.mark.parametrize('names', ['a.mp4\na.mp4\n','missing.mp4\n','../a.mp4\n'])
def test_bad_selection(tmp_path, names):
    root = dataset(tmp_path)
    (root / 'list.txt').write_text(names)
    with pytest.raises(ValueError):
        prepare(root, root / 'list.txt', root / 'out')


def test_release_gallery_metadata_and_video_field(tmp_path):
    root = dataset(tmp_path)
    (root / 'additional_gt').mkdir()
    gallery = [{'chunk_id':name, 'video': f'clips/{name}.mp4', 'dense_caption':'preserve me'}
               for name in ['a','b','c']]
    (root / 'additional_gt/clips_gt.json').write_text(json.dumps({'gallery':gallery}))
    subset, selection = prepare(root, root / 'list.txt', root / 'out')
    assert selection['video_ids'] == ['a','b']
    assert subset['gallery'][0]['dense_caption'] == 'preserve me'
    assert subset['gallery'][0]['video'] == 'clips/a.mp4'


@pytest.mark.parametrize('values', [[[0,0]], [[np.nan,1]], [[np.inf,1]], [1,2]])
def test_invalid_vectors(values):
    with pytest.raises(ValueError):
        validate_vectors(values)


def test_bundle_values_hashes_and_identities(tmp_path):
    root = dataset(tmp_path)
    prepare(root, root / 'list.txt', root / 'out')
    subset, sel = load_inputs(root / 'out/subset.json', root / 'out/selection.json')
    vectors = np.array([[2,3], [4,5]], dtype=np.float32)
    write_bundle(root / 'bundle', subset, sel, vectors, vectors, {'model':'test'})
    bundle = load_bundle(root / 'bundle', subset, sel)
    np.testing.assert_array_equal(bundle['video'], vectors)
    (root / 'bundle/video_ids.json').write_text('["b","b"]')
    with pytest.raises(ValueError):
        load_bundle(root / 'bundle', subset, sel)


def test_https_prefix_auth_and_upload_isolation(monkeypatch, tmp_path):
    import comparison_common as common
    monkeypatch.setenv('AGENT_TOKEN','private')
    calls = []
    class Response:
        status_code = 200
        def raise_for_status(self): pass
    monkeypatch.setattr(common.requests, 'request', lambda method,url,**kw: calls.append((method,url,kw)) or Response())
    ca = tmp_path / 'ca.pem'
    ca.write_text('mock cert')
    net = Network({'agent':'https://host/prefix/api'}, ca_bundle=str(ca))
    net.auth = {'agent': {'bearer_env':'AGENT_TOKEN'}}
    net.request('agent','GET','/v1/videos')
    net.absolute_upload('https://upload/returned?secret=value', method='POST')
    assert calls[0][1] == 'https://host/prefix/api/v1/videos'
    assert calls[0][2]['headers']['Authorization'] == 'Bearer private'
    assert calls[1][2]['headers'] == {}
    assert calls[1][2]['verify'] == str(ca)


@pytest.mark.parametrize('origin', ['http://localhost:7777', 'https://helm.example/tenant'])
def test_deployment_preflight_preserves_routes(monkeypatch, origin):
    import comparison_common as common
    calls = []
    class Response:
        def __init__(self, status, body):
            self.status_code, self.body = status, body
        def json(self): return self.body
    def request(method, url, **kwargs):
        calls.append(url)
        if url.endswith('/openapi.json'):
            return Response(200, {'paths': {'/api/v1/videos': {'post': {}},
                            '/api/v1/videos/{sensor_id}/complete': {'post': {}}}})
        if url.endswith('/generate_text_embeddings'):
            return Response(405,{})
        if url.endswith('/models'):
            return Response(200, {'data':[{'id':'loaded'}]})
        return Response(200, {'index':{}})
    monkeypatch.setattr(common.requests, 'request', request)
    net = Network({'agent':origin+'/api', 'rt_embed':origin+'/rtvi-embed',
                   'elasticsearch':origin+'/elasticsearch'})
    net.preflight(['agent','rt_embed','elasticsearch'])
    net.require_model('loaded')
    net.require_index('profile-*')
    assert origin+'/openapi.json' in calls
    assert origin+'/rtvi-embed/v1/generate_text_embeddings' in calls
    assert origin+'/elasticsearch/profile-*/_mapping' in calls
    assert not any('/api/api/' in url for url in calls)
    with pytest.raises(ValueError, match='does not advertise'):
        net.require_model('wrong')


def test_standard_deployment_parser(tmp_path):
    from comparison_common import load_deployment
    config = tmp_path / 'deployment.json'
    config.write_text(json.dumps({'version':1, 'base_url':'https://host',
                                 'services':{'agent':{'url':'https://host/api'}}}))
    assert load_deployment(config) == {'agent':'https://host/api'}
    config.write_text(json.dumps({'version':99, 'base_url':'https://host','services':{}}))
    with pytest.raises(Exception, match='version'):
        load_deployment(config)

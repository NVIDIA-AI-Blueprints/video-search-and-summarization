# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import sys
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect_vss_embeddings as subject
from comparison_common import read_json, write_json


class Response:
    def __init__(self, body):
        self.body = body
    def json(self):
        return self.body
    def raise_for_status(self):
        pass


class Network:
    def __init__(self):
        self.texts = []
    def preflight(self, services):
        assert services == ['elasticsearch', 'rt_embed']
    def require_model(self, model):
        pass
    def require_index(self, index):
        pass
    def request(self, service, method, path, **kwargs):
        if service == 'elasticsearch':
            return Response({'index': {'mappings': {}}})
        self.texts.append(kwargs['json'])
        return Response({'data': [{'embeddings': [1., 2.]}]})


def hit(vectors):
    return {'_index': 'index', '_id': 'doc', '_source': {'llm': {'visionEmbeddings': vectors}}}


def test_nested_cardinality_and_model_validation():
    entries = [{'vector': [1, 2], 'model': 'cosmos'}, {'vector': [3, 4], 'model': 'cosmos'}]
    assert len(subject.extract_vectors(hit(entries), 'llm.visionEmbeddings.vector', 'cosmos')) == 2
    with pytest.raises(ValueError, match='Indexed model'):
        subject.extract_vectors(hit(entries), 'llm.visionEmbeddings.vector', 'other')
    assert subject.extract_vectors(hit([]), 'llm.visionEmbeddings.vector', 'cosmos') == []


def test_reconstructed_values_distinguished():
    item = {'_index': 'i', '_id': 'd', 'fields': {'v': [[1, 2], [3, 4]]}}
    result = subject.extract_vectors(item, 'v', 'model')
    assert len(result) == 2 and result[0][1]['value_origin'] == 'reconstructed_fields'


def collection_argv(args):
    values = ['--subset', args.subset, '--selection', args.selection,
              '--ingestion', args.ingestion, '--es-index', args.es_index,
              '--model', args.model, '--out', args.out]
    if args.resume:
        values.append('--resume')
    return values


@pytest.mark.parametrize('rejection', ['missing_resume', 'changed_model', 'invalid_subset'])
def test_rejected_cli_preserves_successful_checkpoint(setup, monkeypatch, rejection):
    args, _ = setup
    monkeypatch.setattr(subject, 'scan_sensor', lambda *a: [hit([{'vector': [2., 1.]}])])
    subject.run(args, Network())
    checkpoint = Path(args.out) / 'collection.json'
    bundle = Path(args.out) / 'bundle.json'
    before = checkpoint.read_bytes(), bundle.read_bytes()
    if rejection == 'changed_model':
        args.model = 'other'
        args.resume = True
    elif rejection == 'invalid_subset':
        def invalid(*a):
            raise ValueError('subset fingerprint mismatch')
        monkeypatch.setattr(subject, 'load_inputs', invalid)
    assert subject.main(collection_argv(args)) == 1
    assert (checkpoint.read_bytes(), bundle.read_bytes()) == before


def test_accepted_cli_failure_preserves_rows_and_records_error(setup, monkeypatch):
    args, _ = setup
    monkeypatch.setattr(subject, 'scan_sensor', lambda *a: [hit([{'vector': [2., 1.]}])])
    subject.run(args, Network())
    checkpoint = Path(args.out) / 'collection.json'
    original_texts = read_json(checkpoint)['texts']
    args.resume = True
    monkeypatch.setattr(subject, 'Network', lambda *a, **kw: Network())
    def failure(*a):
        raise TimeoutError('indexing deadline exceeded')
    monkeypatch.setattr(subject, 'scan_sensor', failure)
    assert subject.main(collection_argv(args)) == 1
    state = read_json(checkpoint)
    assert state['complete'] is False
    assert state['texts'] == original_texts
    assert state['error']['type'] == 'TimeoutError'
    assert not (Path(args.out) / 'bundle.json').exists()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    subset = {'meta': {}, 'gallery': [{'chunk_id': 'clip'}], 'queries': [{'query': '  Exact query\ntext  '}]}
    selection = {'schema_version': 1, 'run_id': 'run', 'subset_fingerprint': 'fingerprint', 'video_ids': ['clip'], 'text_ids': ['query']}
    ingestion = tmp_path / 'ingestion.json'
    write_json(ingestion, {**selection, 'clips': [{'dataset_id': 'clip', 'sensor_id': 'sensor'}]})
    args = subject.parser().parse_args(['--subset', 'subset', '--selection', 'selection', '--ingestion', str(ingestion),
                                       '--es-index', 'index-*', '--model', 'cosmos', '--out', str(tmp_path / 'out'),
                                       '--poll-interval', '0.001'])
    monkeypatch.setattr(subject, 'load_inputs', lambda *a: (subset, selection))
    monkeypatch.setattr(subject, 'load_deployment', lambda _: {'rt_embed': 'https://host/embed', 'elasticsearch': 'https://host/es'})
    monkeypatch.setattr(subject, 'embed_text', lambda net, config, model, text: (net.request('rt_embed', 'POST', '/v1/generate_text_embeddings', json={'text_input': [text], 'model': model}).json()['data'][0]['embeddings'], 'test'))
    return args, subset


def test_delayed_index_exact_text_and_resume(setup, monkeypatch):
    args, subset = setup
    attempts = []
    def scan(*a):
        attempts.append(1)
        return [] if len(attempts) == 1 else [hit([{'vector': [2., 1.], 'model': 'cosmos'}])]
    monkeypatch.setattr(subject, 'scan_sensor', scan)
    net = Network()
    subject.run(args, net)
    assert net.texts == [{'text_input': ['  Exact query\ntext  '], 'model': 'cosmos'}]
    assert np.array_equal(np.load(Path(args.out) / 'video.npy'), np.array([[2, 1]], dtype=np.float32))
    args.resume = True
    subject.run(args, net)
    assert len(net.texts) == 1
    assert len(attempts) == 3
    assert read_json(Path(args.out) / 'collection.json')['complete']


def test_multiple_vectors_fail(setup, monkeypatch):
    args, _ = setup
    monkeypatch.setattr(subject, 'scan_sensor', lambda *a: [hit([{'vector': [1, 2]}, {'vector': [2, 1]}])])
    with pytest.raises(ValueError, match='found 2'):
        subject.run(args, Network())


def test_deadline_and_nonfinite_values(setup, monkeypatch):
    args, _ = setup
    args.wait_timeout = 0
    monkeypatch.setattr(subject, 'scan_sensor', lambda *a: [])
    with pytest.raises(TimeoutError):
        subject.run(args, Network())
    args.resume = True
    monkeypatch.setattr(subject, 'scan_sensor', lambda *a: [hit([{'vector': [float('nan'), 1]}])])
    with pytest.raises(ValueError):
        subject.run(args, Network())


def test_model_change_invalidates_resume(setup, monkeypatch):
    args, _ = setup
    monkeypatch.setattr(subject, 'scan_sensor', lambda *a: [hit([{'vector': [1, 2]}])])
    subject.run(args, Network())
    args.resume, args.model = True, 'new'
    with pytest.raises(ValueError, match='dependencies changed'):
        subject.run(args, Network())


def test_cli_defaults():
    args = subject.parser().parse_args(['--subset', 's', '--selection', 's', '--ingestion', 'i', '--es-index', 'i', '--model', 'm', '--out', 'o'])
    assert (args.wait_timeout, args.poll_interval, args.vector_field, args.sensor_field, args.resume) == (1200, 10, 'llm.visionEmbeddings.vector', 'sensor.id.keyword', False)
    with pytest.raises(SystemExit):
        subject.parser().parse_args([])


def test_real_model_metadata_checked():
    item = hit([{'vector': [1, 2]}])
    item['_source']['llm']['info'] = {'modelId': 'cosmos', 'revision': 'immutable'}
    item['_source']['version'] = 'v1'
    vector, provenance = subject.extract_vectors(item, 'llm.visionEmbeddings.vector', 'cosmos')[0]
    assert provenance['model'] == 'cosmos' and provenance['revision'] == 'immutable'
    with pytest.raises(ValueError, match='Indexed model'):
        subject.extract_vectors(item, 'llm.visionEmbeddings.vector', 'other')


def test_scroll_reads_all_pages_and_cleans_up():
    calls = []
    pages = [
        {'_scroll_id': 'scroll1', 'hits': {'total': {'value': 2, 'relation': 'eq'}, 'hits': [hit([{'vector': [1, 2]}])]}},
        {'_scroll_id': 'scroll2', 'hits': {'hits': [hit([{'vector': [2, 1]}])]}},
        {'_scroll_id': 'scroll3', 'hits': {'hits': []}},
    ]
    class ScrollNetwork:
        def request(self, service, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return Response({} if method == 'DELETE' else pages.pop(0))
    rows = list(subject.scan_sensor(ScrollNetwork(), 'index-*', 'sensor.id.keyword', 'sensor', 'llm.visionEmbeddings.vector'))
    assert len(rows) == 2 and not pages
    assert calls[0][2]['json']['query'] == {'term': {'sensor.id.keyword': 'sensor'}}
    assert calls[-1][2]['json'] == {'scroll_id': ['scroll3']}


def test_failed_shards_reject_partial_export():
    class BrokenNetwork:
        def request(self, *a, **kwargs):
            return Response({'_shards': {'failed': 1}, 'hits': {'hits': []}})
    with pytest.raises(RuntimeError, match='failed shards'):
        list(subject.scan_sensor(BrokenNetwork(), 'index', 'sensor', 'id', 'v'))


def test_nested_reconstructed_fields_cardinality():
    item = {'_index': 'i', '_id': 'd', 'fields': {'llm.visionEmbeddings': [
        {'vector': [1, 2]}, {'vector': [3, 4]}]}}
    result = subject.extract_vectors(item, 'llm.visionEmbeddings.vector', 'model')
    assert len(result) == 2
    assert all(row[1]['value_origin'] == 'reconstructed_fields' for row in result)

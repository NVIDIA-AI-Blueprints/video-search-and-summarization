# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import math
from pathlib import Path
import socket
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows.clip_scoring import ClipScorer, aggregate, verdict
from flows.capture import atomic_json, digest, recover, run, seal
from flows.query import CliQueryBackend
from replay_campaign import replay
from run_capture_campaign import Backend
import run_eval_flows as runner


def fixture(size=4, relevant=('a', 'c')):
    mapping = {chr(97+i): {'sensor_id': str(i), 'filename': chr(97+i)+'.mp4'} for i in range(size)}
    q = {'query_id': 'q1', 'query_domain': 'event', 'query': 'original query', 'relevant_clip_ids': list(relevant)}
    return mapping, q, ClipScorer(mapping, [q])


def test_sparse_ap_and_ndcg_use_complete_ground_truth():
    _, q, scorer = fixture()
    hits = [{'clip_id': 'a', 'similarity': 0.99999999}, {'clip_id': 'a', 'similarity': 1.0}, {'clip_id': 'b'}]
    result = scorer.score(q, hits)
    assert result['ranking'] == ['a', 'b']
    assert result['average_precision'] == .5
    assert result['ap_at_10'] == .5
    assert result['at_k'][10]['precision'] == .1
    assert result['precision'] == .5
    assert result['ndcg_at_10'] == pytest.approx(1/(1+1/math.log2(3)))
    assert result['duplicate_hits'] == 1
    assert aggregate([result])['mAP_scope'].startswith('observed')


def test_more_relevance_than_cutoff():
    _, q, scorer = fixture(20, tuple(chr(97+i) for i in range(20)))
    r = scorer.score(q, [{'clip_id': chr(97+i)} for i in range(10)])
    assert r['average_precision'] == .5
    assert r['ndcg_at_10'] == 1
    assert r['at_k'][10]['recall'] == .5


def test_critic_filters_before_dedup_and_never_backfills():
    _, q, scorer = fixture()
    hits = [{'clip_id':'a', 'verification':{'result':'rejected'}},
            {'clip_id':'b', 'verification':{'result':'rejected'}},
            {'clip_id':'a', 'verification':None}, {'clip_id':'c'}]
    before = json.dumps(hits)
    r = scorer.pair(q, hits, 12.3456789)
    assert r['critic_filtered']['ranking'] == ['a', 'c']
    assert r['critic_filtered']['average_precision'] == 1
    assert r['critic_effect'] == {'relevant_clips_removed':0, 'irrelevant_clips_removed':1}
    assert r['critic'] == {'rejected':2, 'null':1, 'missing':1}
    assert r['raw']['latency_s'] == 12.3456789
    assert json.dumps(hits) == before


@pytest.mark.parametrize('hit,state', [({}, 'missing'), ({'verification':None}, 'null'),
    ({'verification':{}}, 'missing-result'), ({'verification':{'result':'unverified'}}, 'unverified'),
    ({'critic_result':{'result':'rejected'}}, 'rejected'), ({'verification':'accepted'}, 'invalid')])
def test_verdicts(hit, state):
    assert verdict(hit) == state


@pytest.mark.parametrize('hits', [[{'clip_id':'unknown'}], [{'sensor_id':'0','video_name':'b'}], [{}]])
def test_unknown_or_conflicting_identities_fail(hits):
    _, q, scorer = fixture()
    with pytest.raises(ValueError): scorer.score(q, hits)


def test_empty_and_complete_rankings():
    _, q, scorer = fixture()
    empty = scorer.score(q, [])
    assert empty['average_precision'] == empty['ndcg'] == empty['reciprocal_rank'] == 0
    complete = scorer.score(q, [{'clip_id': cid} for cid in 'abcd'])
    assert complete['average_precision'] == pytest.approx((1+2/3)/2)
    assert aggregate([complete])['mAP_scope'] == 'full-gallery'


@pytest.mark.parametrize('path,dec', [('embed', {'attributes':[]}),
    ('attribute', {'attributes':['red shirt'], 'has_action':False}),
    ('fusion', {'attributes':['red shirt'], 'has_action':True}),
    ('object', {'object_ids':['42']})])
def test_all_paths_gallery_and_critic_budget(path, dec):
    b = CliQueryBackend(['vss'], decompositions={'original': {**dec, 'top_k': 2}})
    b.evaluation_top_k = b.critic_eval_count = 1000
    b.evaluation_gallery_sources = ['a','b']
    b.evaluation_no_persist = True
    argv = b.build_argv('original')
    assert argv[3] == path
    assert argv[argv.index('--top-k')+1] == '1000'
    assert argv[argv.index('--critic-eval-count')+1] == '1000'
    assert argv[argv.index('--original-query')+1] == 'original'
    assert '--no-persist' in argv


def test_campaign_paths_share_filters_and_decomposition():
    dec = {'query':'rewritten', 'attributes':[], 'video_sources':['a'], 'timestamp_start':'2025-01-01T00:00:00Z'}
    live = Backend('live-routed', 'original', ['a'], dec)
    forced = Backend('forced-fusion', 'original', ['a'], dec)
    assert {**live.plan, 'path':'fusion'} == forced.plan
    assert live.plan['top_k'] == 1000
    assert live.critic_eval_count == 1000
    assert '--attribute' not in forced.build_argv('original')
    attribute = Backend('attribute', 'Exact original text!', ['a'])
    argv = attribute.build_argv('Exact original text!')
    assert argv[argv.index('--attribute')+1] == 'Exact original text!'


def test_defaults_and_explicit_legacy():
    base = ['--endpoint','http://example','--clip-data','gt.json','--ingestion-manifest','ingestion.json','--skip-ingest']
    args = runner.parse_args(base)
    assert (args.scoring, args.retrieval_depth, args.top_k, args.min_cosine_similarity) == ('clips','gallery',1000,-1)
    assert runner.parse_args(base + ['--retrieval-depth','top-k','--top-k','10']).top_k == 10
    with pytest.raises(SystemExit): runner.parse_args(base + ['--top-k','10'])
    with pytest.raises(SystemExit): runner.parse_args(base + ['--retrieval-depth','top-k','--top-k','0'])
    with pytest.raises(SystemExit): runner.parse_args(['--endpoint','http://example'])
    assert runner.parse_args(['--endpoint','http://example','--scoring','segments']).top_k == 5


def test_offline_matches_online_without_network_or_processes(tmp_path, monkeypatch):
    mapping, q, scorer = fixture()
    campaign = tmp_path/'campaign'; snapshot = campaign/'snapshot'
    atomic_json(snapshot/'queries.json', [q])
    atomic_json(snapshot/'gallery-mapping.json', mapping)
    seal(snapshot, ['queries.json','gallery-mapping.json'])
    atomic_json(campaign/'campaign.json', {'campaign_id':'test','inputs':{'settings':{'modes':['embed'],'top_k':10}}})
    atomic_json(campaign/'identity.json', {'sha256':digest(campaign/'campaign.json')})
    attempt = campaign/'attempts/embed/q1/a1'
    hits = [{'clip_id':'a','verification':{'result':'rejected'}},{'clip_id':'b'},{'clip_id':'c'}]
    run(attempt, [sys.executable,'-c','print('+repr(json.dumps({'data':hits, 'unknown':{'preserved':True}}))+')'],
        cwd=None, timeout=10, metadata={'plan':{'path':'embed','top_k':10}})
    recover(attempt)
    latency = json.loads((attempt/'process.json').read_text())['elapsed_s']
    expected = scorer.pair(q, hits, latency)
    before = {str(p):digest(p) for p in campaign.rglob('*') if p.is_file()}
    def forbidden(*a, **kw): raise AssertionError('Offline replay attempted inference/network')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    report = replay(campaign,tmp_path/'report')
    row = report['modes']['embed']['queries'][0]
    assert row['raw'] == expected['raw']
    assert row['critic_filtered'] == expected['critic_filtered']
    assert report['modes']['embed']['groups']['all']['mean_latency_s'] == latency
    assert before == {str(p):digest(p) for p in campaign.rglob('*') if p.is_file()}


def test_complete_ap_matches_original_drive():
    import importlib.util
    import os
    directory = os.environ.get('DRIVE_SCRIPTS_DIR')
    if not directory:
        pytest.skip('Set DRIVE_SCRIPTS_DIR to compare original Drive implementation')
    import numpy as np
    spec = importlib.util.spec_from_file_location('original_drive', Path(directory)/'evaluate_domain_test.py')
    drive = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(drive)
    _, q, scorer = fixture()
    for ranking in ('abcd', 'dcba', 'bdac'):
        labels = np.array([int(cid in q['relevant_clip_ids']) for cid in ranking])
        result = scorer.score(q, [{'clip_id':cid} for cid in ranking])
        assert result['average_precision'] == pytest.approx(drive.average_precision(labels))
        assert result['ndcg_at_10'] == pytest.approx(drive.ndcg_at_k(labels, 10))


def test_uploaded_display_filename_uses_authoritative_sensor():
    _, q, scorer = fixture()
    r = scorer.score(q, [{'sensor_id':'0','video_name':'a_20261009_upload.mp4'}])
    assert r['ranking'] == ['a']
    with pytest.raises(ValueError):
        scorer.score(q, [{'video_name':'a_20261009_upload.mp4'}])


def test_online_capture_is_replayable_and_fixed_attribute_stays_attribute(tmp_path, monkeypatch):
    from flows import clip_evaluation
    mapping, q, _ = fixture()
    ingestion = tmp_path/'ingestion.json'
    atomic_json(ingestion, {'clips':[{'dataset_id':cid, **row} for cid,row in mapping.items()]})
    args = runner.parse_args(['--endpoint','http://example', '--clip-data','unused.json',
        '--ingestion-manifest',str(ingestion),'--skip-ingest','--fixed-search-path',
        '--search-path','attribute','--output-file',str(tmp_path/'online.json')])
    hits = [{'sensor_id':'0','verification':{'result':'rejected'}}, {'sensor_id':'2'}]
    backend = CliQueryBackend([sys.executable,'-c','print('+repr(json.dumps({'data':hits}))+')'], search_path='attribute')
    monkeypatch.setattr(clip_evaluation,'deployment_identity',lambda *a: {'inventory':'test'})
    report = clip_evaluation.run(args, backend, [q], mapping)
    assert list(report['modes']) == ['attribute']
    row = report['modes']['attribute']['queries'][0]
    assert row['raw']['average_precision'] == 1
    assert row['critic_filtered']['average_precision'] == .5
    capture = tmp_path/'online-capture'
    request = json.loads(next(capture.glob('attempts/*/*/*/request.json')).read_text())
    assert request['plan']['attributes'] == [q['query']]
    assert request['plan']['top_k'] == 1000
    assert '--no-persist' in request['argv']
    def forbidden(*a, **kw): raise AssertionError('Offline attempted inference')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    replayed = replay(capture, tmp_path/'offline-again')
    assert replayed['modes'] == report['modes']


def test_resume_dry_run_keeps_old_collection_settings(tmp_path, monkeypatch, capsys):
    import run_capture_campaign as campaign
    old = {'top_k':10,'critic_eval_count':10,'min_cosine_similarity':0,'modes':list(campaign.MODES)}
    atomic_json(tmp_path/'campaign/campaign.json', {'inputs':{'settings':old}})
    atomic_json(tmp_path/'ingestion.json', {})
    root = tmp_path/'dataset'; root.mkdir()
    queries = [{'query_id':'q1','query':'test','query_domain':'event'}]
    mapping = {'a':{'filename':'a.mp4'}}
    monkeypatch.setattr(campaign, 'dataset', lambda *args: (queries, mapping))
    monkeypatch.setattr(sys, 'argv', ['run_capture_campaign.py','--dataset-root',str(root),
        '--ingestion-manifest',str(tmp_path/'ingestion.json'),'--output-dir',str(tmp_path/'campaign'),
        '--llm-url','http://example/llm','--llm-model','test','--resume','--dry-run'])
    campaign.main()
    result = json.loads(capsys.readouterr().out)
    assert result['settings'] == old
    for argv in result['example_argv'].values():
        assert argv[argv.index('--top-k')+1] == '10'
        assert argv[argv.index('--critic-eval-count')+1] == '10'


def test_gallery_request_cap_is_reported(tmp_path, monkeypatch):
    from flows import clip_evaluation
    mapping, q, _ = fixture()
    atomic_json(tmp_path/'ingestion.json', {'clips':[{'dataset_id':cid, **v} for cid,v in mapping.items()]})
    args = runner.parse_args(['--endpoint','http://example','--clip-data','unused',
        '--ingestion-manifest',str(tmp_path/'ingestion.json'),'--skip-ingest','--fixed-search-path',
        '--retrieval-depth','top-k','--top-k','1','--output-file',str(tmp_path/'cap.json')])
    backend = CliQueryBackend([sys.executable,'-c','print(\'{"data":[{"sensor_id":"0"}]}\')'])
    monkeypatch.setattr(clip_evaluation,'deployment_identity',lambda *a: {})
    result = clip_evaluation.run(args,backend,[q],mapping)
    group = result['modes']['embed']['groups']['all']
    assert group['queries_at_request_cap'] == 1
    assert group['raw']['complete_rankings'] == 0
    assert group['raw']['mAP_scope'].startswith('observed')

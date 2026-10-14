# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows.capture import (atomic_json, digest, read_json, recover, response, run, seal, verify, IntegrityError)
from flows.query import CliQueryBackend, CliExitError, QueryUnanswerableError
from run_capture_campaign import Backend, parse_decomposition
from replay_campaign import replay, verdict, successful


def execute(tmp_path, script, timeout=10):
    path = tmp_path/'attempt'
    proc, latency = run(path, [sys.executable,'-c',script], cwd=None, timeout=timeout,
                         metadata={'query_id':'q1','plan':{'path':'embed'}})
    return path,proc,latency


def test_lossless_multiline_ndjson(tmp_path):
    hits = [{'video_name':'clip', 'similarity':0.12345678912345678, 'unknown':[1,None]},
            {'video_name':'clip', 'similarity':0.12345678912345679, 'critic_result':None},
            {'verification':{'result':'unverified'}}, {'verification':{'result':'rejected'}}]
    wire = json.dumps({'data':hits,'unknown':{'nested':123},'timings':{'search':1.23}},indent=2)+'\n'+json.dumps({'event':'done','job_id':'abc'})+'\n'
    path,proc,latency = execute(tmp_path, 'import sys;sys.stdout.write('+repr(wire)+');sys.stderr.write("diagnostic")')
    captured,process = recover(path)
    assert (path/'stdout.bin').read_bytes() == wire.encode()
    assert captured['envelope']['data'] == hits
    assert len(captured['documents']) == 2
    assert process['elapsed_s'] == latency
    assert (path/'stderr.bin').read_bytes() == b'diagnostic'
    assert [verdict(h) for h in hits] == ['missing','null','unverified','rejected']
    assert recover(path) == (captured,process)
    (path/'stdout.bin').write_bytes(b'changed')
    with pytest.raises(IntegrityError): verify(path)


@pytest.mark.parametrize('wire', ['', 'garbage', '{"data":null}', '{"data":{}}', '{"data":[', '{"data":[2]}', '{"event":"done"}'])
def test_malformed_not_empty(wire):
    with pytest.raises(ValueError): response(wire)


def test_empty_and_reparse(tmp_path):
    path,_,_=execute(tmp_path, 'print(\'{"data":[],"extra":true}\')')
    assert not (path/'complete.json').exists()
    assert recover(path)[0]['envelope'] == {'data':[],'extra':True}
    before = (path/'complete.json').read_bytes()
    recover(path)
    assert before == (path/'complete.json').read_bytes()


def test_failure_and_partial_timeout(tmp_path):
    path,proc,_=execute(tmp_path,'import sys;print("partial",flush=True);sys.exit(3)')
    assert proc.returncode == 3
    with pytest.raises(ValueError): recover(path)
    other=tmp_path/'timeout'
    with pytest.raises(subprocess.TimeoutExpired):
        run(other,[sys.executable,'-c','import time;print("partial",flush=True);time.sleep(5)'],cwd=None,timeout=1,metadata={})
    assert (other/'stdout.bin').read_bytes() == b'partial\n'
    assert read_json(other/'process.json')['error']['timeout']
    assert not (other/'complete.json').exists()


def test_interruption_and_write_failure(tmp_path):
    with patch('flows.capture.subprocess.run', side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            execute(tmp_path,'')
    assert read_json(tmp_path/'attempt/process.json')['error']['cancelled']
    with patch('flows.capture.atomic_json',side_effect=OSError('disk full')), patch('flows.capture.subprocess.run') as child:
        with pytest.raises(OSError):
            run(tmp_path/'disk',[],cwd=None,timeout=1,metadata={})
        child.assert_not_called()


def test_query_contract_and_flags(tmp_path):
    original = '  A person, wearing a blue shirt.  '
    attribute = Backend('attribute',original,['gallery'])
    argv=attribute.build_argv(original)
    assert argv[argv.index('--attribute')+1] == original
    assert argv.count('--attribute') == 1 and '--query' not in argv
    dec={'query':'rewritten','attributes':[],'has_action':True,'video_sources':['model-source'],
         'timestamp_start':'2025-01-01T00:00:00Z','timestamp_end':'2025-01-01T00:00:05Z'}
    live=Backend('live-routed',original,['gallery'],dec)
    forced=Backend('forced-fusion',original,['gallery'],dec)
    assert {**live.plan,'path':'fusion'} == forced.plan
    assert '--attribute' not in forced.build_argv(original)
    assert forced.build_argv(original)[3] == 'fusion'
    b=CliQueryBackend([sys.executable,'-c','print(\'{"data":[]}\')'],capture_directory=str(tmp_path/'backend'))
    assert b.search('hello')[0] == []
    assert read_json(tmp_path/'backend/request.json')['original_query']=='hello'


def test_decomposition_requires_complete_response():
    with pytest.raises(ValueError):
        parse_decomposition({'choices':[{'finish_reason':'length'}]})
    with pytest.raises(ValueError):
        parse_decomposition({'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]})


def test_offline_replay_preserves_latency_and_denominators(tmp_path, monkeypatch):
    campaign=tmp_path/'campaign'; snapshot=campaign/'snapshot'; snapshot.mkdir(parents=True)
    q={'query_id':'q1','query_domain':'event','query':'test','segments':[{
        'video_name':'clip','start_time':'2025-01-01T00:00:00Z','end_time':'2025-01-01T00:00:05Z'}]}
    atomic_json(snapshot/'queries.json',[q]);seal(snapshot,['queries.json'])
    atomic_json(campaign/'campaign.json',{'campaign_id':'test'})
    atomic_json(campaign/'identity.json',{'sha256':digest(campaign/'campaign.json')})
    attempt=campaign/'attempts/embed/q1/attempt1'
    hit={**q['segments'][0],'verification':{'result':'rejected'}}
    run(attempt,[sys.executable,'-c','print('+repr(json.dumps({'data':[hit]}))+')'],cwd=None,timeout=10,
        metadata={'query_id':'q1','plan':{'path':'embed'}})
    recover(attempt)
    saved=read_json(attempt/'process.json')['elapsed_s']
    def forbidden(*args,**kwargs): raise AssertionError('Network/CLI invoked by offline replay')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    r=replay(campaign,tmp_path/'report', scorer='native')
    group=r['modes']['embed']['groups']['all']
    assert group['raw']['mAP'] == 1
    assert group['critic_filtered']['mAP'] == 0
    assert group['mean_latency_s'] == saved
    assert r['modes']['attribute']['groups']['all']['answered'] == 0
    assert r['modes']['attribute']['groups']['all']['raw']['mAP'] is None
    assert successful(campaign,'embed','q1')==attempt
    with pytest.raises(ValueError):replay(campaign,campaign/'bad')


@pytest.mark.parametrize('attributes', ['missing', None, []])
def test_product_optional_attributes_preserved(attributes):
    value = {'query': 'fire spreading through a structure', 'has_action': True,
             'source_type': 'video_file'}
    if attributes != 'missing':
        value['attributes'] = attributes
    parsed = parse_decomposition({'choices': [{'finish_reason': 'stop',
        'message': {'content': json.dumps(value)}}]})
    assert parsed == value
    live = Backend('live-routed', 'original', ['gallery'], parsed).plan
    fusion = Backend('forced-fusion', 'original', ['gallery'], parsed).plan
    assert live['attributes'] == [] and live['path'] == 'embed'
    assert {**live, 'path': 'fusion'} == fusion


@pytest.mark.parametrize('attributes', ['person', {}, [1]])
def test_malformed_attributes_still_rejected(attributes):
    with pytest.raises(ValueError, match='Invalid attributes'):
        parse_decomposition({'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps({'query': 'q', 'has_action': True, 'attributes': attributes})}}]})


def test_resume_requires_exact_sealed_amendment(tmp_path):
    import copy
    from run_capture_campaign import validate_resume_identity
    atomic_json(tmp_path/'before.py', {'old': True})
    atomic_json(tmp_path/'after.py', {'new': True})
    original = {'settings': {'top_k': 10}, 'code_sha256': {'runner.py': digest(tmp_path/'before.py')}}
    atomic_json(tmp_path/'campaign.json', {'inputs': original})
    validate_resume_identity(tmp_path, original)
    changed = copy.deepcopy(original)
    changed['code_sha256']['runner.py'] = digest(tmp_path/'after.py')
    with pytest.raises(ValueError, match='Incompatible'):
        validate_resume_identity(tmp_path, changed)
    amendment = tmp_path/'amendments/001'
    amendment.mkdir(parents=True)
    (amendment/'before.py').write_bytes((tmp_path/'before.py').read_bytes())
    (amendment/'after.py').write_bytes((tmp_path/'after.py').read_bytes())
    atomic_json(amendment/'amendment.json', {'campaign_sha256': digest(tmp_path/'campaign.json'),
        'changes': [{'path': 'runner.py', 'before_file': 'before.py', 'after_file': 'after.py',
            'before_sha256': digest(amendment/'before.py'), 'after_sha256': digest(amendment/'after.py')}]})
    seal(amendment, ['amendment.json', 'before.py', 'after.py'])
    validate_resume_identity(tmp_path, changed)
    changed['settings']['top_k'] = 5
    with pytest.raises(ValueError, match='Incompatible'):
        validate_resume_identity(tmp_path, changed)
    (amendment/'after.py').write_text('tampered')
    with pytest.raises(IntegrityError):
        validate_resume_identity(tmp_path, changed)


def test_recover_saved_optional_decomposition_without_inference(tmp_path):
    from types import SimpleNamespace
    from run_capture_campaign import decompose
    previous = tmp_path/'decompositions/event_0512/attempt1'
    previous.mkdir(parents=True)
    value = {'query': 'fire spreading through a structure', 'has_action': True, 'source_type': 'video_file'}
    atomic_json(previous/'request.json', {'query_id': 'event_0512'})
    atomic_json(previous/'response.bin', {'choices': [{'finish_reason': 'stop',
        'message': {'content': json.dumps(value)}}]})
    atomic_json(previous/'process.json', {'status': 200, 'error': None, 'elapsed_s': 1.23,
        'response_sha256': digest(previous/'response.bin')})
    args = SimpleNamespace(output_dir=tmp_path)
    with patch('requests.post', side_effect=AssertionError('Inference must not repeat')):
        parsed, ref = decompose(args, {'query_id': 'event_0512'}, [])
        again, same_ref = decompose(args, {'query_id': 'event_0512'}, [])
    assert parsed == again == value and ref == same_ref
    assert read_json(previous/'process.json')['elapsed_s'] == 1.23
    verify(previous)

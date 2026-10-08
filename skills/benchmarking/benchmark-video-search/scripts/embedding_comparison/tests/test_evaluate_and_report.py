# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import sys

import numpy as np
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import evaluate_and_report as report


def test_agreement_float64_math_preserves_raw_values():
    reference=np.array([[3,4],[1,0]],dtype=np.float32)
    vss=np.array([[6,8],[0,1]],dtype=np.float32)
    before=reference.copy()
    rows, summary=report.agreement(reference,vss,['a','b'])
    assert rows[0]['cosine_similarity']==pytest.approx(1)
    assert rows[0]['raw_l2']==5
    assert rows[0]['normalized_l2']==0
    assert rows[0]['reference_norm']==5
    assert rows[0]['vss_norm']==10
    assert rows[0]['max_absolute_difference']==4
    assert rows[1]['normalized_l2']==pytest.approx(np.sqrt(2))
    assert summary['cosine_similarity']['mean']==pytest.approx(.5)
    np.testing.assert_array_equal(reference,before)
    np.testing.assert_allclose(report.cosine_matrix(reference,vss),[[1,.8],[.6,0]])


def test_align_exact_identities_and_reject_incomplete():
    bundle={'text_ids':['q2','q1'],'video_ids':['v2','v1'],'text':np.array([[0,1],[1,0]]),'video':np.array([[0,1],[1,0]])}
    result=report.align(bundle,['q1','q2'],['v1','v2'])
    np.testing.assert_array_equal(result['text'],[[1,0],[0,1]])
    with pytest.raises(ValueError,match='Incomplete'):
        report.align(bundle,['q1'],['v1','v2'])


def test_empty_slice_unavailable():
    values=report.flatten_metrics({'overall':{'n':2,'recall':.5},'empty':{'n':0,'recall':0},'nan':float('nan')})
    assert values['overall/recall']==.5
    assert values['empty'] is None
    assert 'empty/recall' not in values
    assert values['nan'] is None


def test_original_event_summary_empty_dictionary_is_unavailable():
    values = report.flatten_metrics({'slices': {'empty': {}, 'event': {'num_queries': 1, 'mAP': .5}}})
    assert values['slices/empty'] is None
    assert values['slices/event/mAP'] == .5


def test_external_discovery_unique_and_fail_closed(tmp_path):
    script=tmp_path/'score.py'
    script.write_text("import argparse\np=argparse.ArgumentParser()\nfor k in ['subset','text','video','out']: p.add_argument('--'+k)\np.parse_args()\n")
    assert report.discover_script(tmp_path,sys.executable,('subset','text','video','out'))==script
    (tmp_path/'other.py').write_text(script.read_text())
    with pytest.raises(ValueError,match='exactly one'):
        report.discover_script(tmp_path,sys.executable,('subset','text','video','out'))


def test_event_discovery_rejects_multiple_supported_interfaces(tmp_path):
    (tmp_path / 'from_embeddings.py').write_text(
        "import argparse\np=argparse.ArgumentParser()\n"
        "for k in ['data','emb-dir','out']: p.add_argument('--'+k)\np.parse_args()\n")
    (tmp_path / 'from_metrics.py').write_text(
        "import argparse\np=argparse.ArgumentParser()\n"
        "for k in ['metrics','out']: p.add_argument('--'+k)\np.parse_args()\n")
    with pytest.raises(ValueError, match='exactly one'):
        report.discover_event_script(tmp_path, sys.executable)


def test_event_discovery_counts_dual_interface_script_once(tmp_path):
    script = tmp_path / 'events.py'
    script.write_text("import argparse\np=argparse.ArgumentParser()\n"
                      "for k in ['data','emb-dir','metrics','out']: p.add_argument('--'+k)\np.parse_args()\n")
    assert report.discover_event_script(tmp_path, sys.executable) == script


def test_cli_defaults_and_requirements():
    with pytest.raises(SystemExit): report.parser().parse_args([])
    args=report.parser().parse_args(['--subset','s','--vss-dir','v','--reference-dir','r','--scripts-dir','d','--out','o'])
    assert args.python==sys.executable
    assert args.log_level=='INFO'


def test_report_invokes_both_original_scorers_and_writes_every_comparison(tmp_path, monkeypatch):
    import json
    scripts=tmp_path/'scripts'; scripts.mkdir()
    (scripts/'score.py').write_text('''import argparse,json,pathlib
p=argparse.ArgumentParser()
for k in ['subset','text','video','out']: p.add_argument('--'+k,required=True)
a=p.parse_args(); o=pathlib.Path(a.out); o.mkdir(exist_ok=True,parents=True)
(o/'metrics.json').write_text(json.dumps({'overall':{'n':1,'recall':1.}}))
(o/'rankings.json').write_text('[]')
''')
    (scripts/'events.py').write_text('''import argparse,json,pathlib
p=argparse.ArgumentParser()
for k in ['metrics','out']: p.add_argument('--'+k,required=True)
a=p.parse_args(); o=pathlib.Path(a.out); o.mkdir(exist_ok=True,parents=True)
(o/'event_summary.json').write_text(json.dumps({'action':{'n':1,'recall':1.},'empty':{'n':0,'recall':0.}}))
''')
    subset=tmp_path/'subset.json'; subset.write_text(json.dumps({'queries':[{'query_id':'q1'},{'query_id':'q2'}],'gallery':[{'chunk_id':'clip'}]}))
    def load_bundle(directory, subset):
        text_ids=['q2','q1'] if str(directory)=='reference' else ['q1','q2']
        return {'text':np.array([[0.,1.],[1.,0.]],dtype=np.float32),'video':np.array([[1.,0.]],dtype=np.float32),
                'text_ids':text_ids,'video_ids':['clip'],
                'manifest':{'run_id':'run','subset_fingerprint':'fingerprint','provenance':{'model':'same-model','revision':'a'*40}}}
    monkeypatch.setattr(report,'load_bundle',load_bundle)
    out=tmp_path/'out'
    args=report.parser().parse_args(['--subset',str(subset),'--vss-dir','vss','--reference-dir','reference',
                                   '--scripts-dir',str(scripts),'--out',str(out)])
    report.evaluate(args)
    summary=json.loads((out/'summary.json').read_text())
    assert summary['agreement']['text']['cosine_similarity']['mean']==0
    assert summary['checkpoint_parity']=='verified'
    assert (out/'reference'/'retrieval'/'rankings.json').exists()
    assert (out/'vss'/'events'/'event_summary.json').exists()
    for approach, expected in [('reference', [[1.,0.],[0.,1.]]), ('vss', [[0.,1.],[1.,0.]])]:
        command=summary['executions'][approach]['retrieval']['command']
        text_path=Path(command[command.index('--text')+1])
        video_path=Path(command[command.index('--video')+1])
        assert text_path.parent.parent.name == approach
        assert video_path.parent.parent.name == approach
        np.testing.assert_array_equal(np.load(text_path), expected)
        np.testing.assert_array_equal(np.load(video_path), [[1.,0.]])
    np.testing.assert_array_equal(np.load(out/'similarity_difference.npy'),[[-1.],[1.]])
    assert all((out/name).exists() for name in ['text_agreement.csv','video_agreement.csv','retrieval_comparison.csv','summary.md'])


def test_original_drive_cli_uses_aligned_embeddings_and_summary_output_file(tmp_path, monkeypatch):
    import json
    import run_embedding_comparison as runner
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    (scripts / 'embed_cosmos.py').write_text(
        "import argparse\np=argparse.ArgumentParser()\n"
        "for k in ['data','out','model','revision','num-frames','video-batch','text-batch']: "
        "p.add_argument('--'+k)\np.parse_args()\n")
    (scripts / 'evaluate_domain_test.py').write_text('''import argparse,json,pathlib,numpy as np
p=argparse.ArgumentParser()
p.add_argument('--data','--gt',required=True)
p.add_argument('--text-emb',required=True)
p.add_argument('--video-emb',required=True)
p.add_argument('--video-ids',required=True)
p.add_argument('-o','--out-dir',required=True)
a=p.parse_args()
assert json.loads(pathlib.Path(a.video_ids).read_text()) == ['v1','v2']
assert np.load(a.text_emb).tolist() == [[1.,0.],[0.,1.]]
assert np.load(a.video_emb).tolist() == [[1.,0.],[0.,1.]]
o=pathlib.Path(a.out_dir); o.mkdir(exist_ok=True,parents=True)
(o/'metrics.json').write_text(json.dumps({'overall':{'num_queries':2,'mAP':1.}}))
(o/'rankings.jsonl').write_text('{}\\n')
''')
    (scripts / 'summarize_event_slices.py').write_text('''import argparse,json,pathlib,numpy as np
p=argparse.ArgumentParser()
p.add_argument('--data','--gt',required=True)
p.add_argument('--emb-dir',required=True)
p.add_argument('--out',required=True)
a=p.parse_args(); i=pathlib.Path(a.emb_dir)
assert json.loads((i/'video_ids.json').read_text()) == ['v1','v2']
assert np.load(i/'text.npy').tolist() == [[1.,0.],[0.,1.]]
assert np.load(i/'video.npy').tolist() == [[1.,0.],[0.,1.]]
o=pathlib.Path(a.out); o.parent.mkdir(parents=True,exist_ok=True)
o.write_text(json.dumps({'groups':{'event_only':{'num_queries':2,'mAP':1.}},'slices':{'empty':{}}}))
''')
    subset = tmp_path / 'subset.json'
    subset.write_text(json.dumps({'queries': [{'query_id': 'q1'}, {'query_id': 'q2'}],
                                 'gallery': [{'chunk_id': 'v1'}, {'chunk_id': 'v2'}]}))
    def bundle(directory, subset):
        return {'text_ids': ['q2', 'q1'], 'video_ids': ['v2', 'v1'],
                'text': np.array([[0., 1.], [1., 0.]], dtype=np.float32),
                'video': np.array([[0., 1.], [1., 0.]], dtype=np.float32),
                'manifest': {'run_id': 'run', 'subset_fingerprint': 'fingerprint', 'provenance': {}}}
    monkeypatch.setattr(report, 'load_bundle', bundle)
    runner.preflight_external({'scripts_dir': str(scripts), 'python': sys.executable})
    out = tmp_path / 'out'
    args = report.parser().parse_args(['--subset', str(subset), '--vss-dir', 'vss',
                                      '--reference-dir', 'reference', '--scripts-dir', str(scripts),
                                      '--out', str(out)])
    report.evaluate(args)
    summary = json.loads((out / 'summary.json').read_text())
    for approach in ('reference', 'vss'):
        command = summary['executions'][approach]['events']['command']
        assert Path(command[command.index('--emb-dir') + 1]) == out / approach / 'inputs'
        assert Path(command[command.index('--out') + 1]) == out / approach / 'events' / 'event_summary.json'
        assert (out / approach / 'retrieval' / 'rankings.jsonl').is_file()
        assert (out / approach / 'events' / 'execution.json').is_file()
    row = next(row for row in summary['retrieval_comparison'] if row['metric'] == 'events/slices/empty')
    assert row['reference'] is None and row['vss'] is None and row['vss_minus_reference'] is None
    assert summary['agreement']['video']['cosine_similarity']['mean'] == 1.

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import sys

import numpy as np
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate_reference_embeddings as reference
from comparison_common import sha256_file
from prepare_subset import prepare


def fake_script(path, fail=False):
    path.write_text('''import argparse,json,pathlib,numpy as np
p=argparse.ArgumentParser()
for k in ['subset','out','model','revision','num-frames','video-batch','text-batch']: p.add_argument('--'+k,required=True)
a=p.parse_args()
o=pathlib.Path(a.out); o.mkdir(exist_ok=True,parents=True)
''' + ("raise RuntimeError('mock failure')\n" if fail else '''np.save(o/'text.npy',np.array([[1.,2.]],dtype=np.float32))
np.save(o/'video.npy',np.array([[0.,1.],[1.,0.]],dtype=np.float32))
(o/'video_ids.json').write_text(json.dumps(['b','a']))
(o/'sanity.json').write_text('{}')
'''))


def test_reference_external_command_publishes_and_aligns(tmp_path, monkeypatch):
    scripts = tmp_path / 'scripts'; scripts.mkdir()
    fake_script(scripts / 'embed_cosmos.py')
    selection = {'video_ids': ['a','b'], 'text_ids': ['q'], 'clips': media_clips(tmp_path)}
    monkeypatch.setattr(reference, 'load_inputs', lambda *_: ({}, selection))
    monkeypatch.setattr(reference, 'environment_versions', lambda *_: {})
    captured = {}
    def write_bundle(out, subset, selection, text, video, provenance):
        captured.update(text=text, video=video, provenance=provenance)
        (out / 'bundle.json').write_text('{}')
    monkeypatch.setattr(reference, 'write_bundle', write_bundle)
    args = reference.parser().parse_args(['--subset',str(tmp_path/'subset.json'),'--selection',str(tmp_path/'selection.json'),
        '--scripts-dir',str(scripts),'--model','model','--revision','a'*40,'--out',str(tmp_path/'out')])
    reference.generate(args)
    np.testing.assert_array_equal(captured['video'], [[1,0],[0,1]])
    assert (tmp_path/'out'/'original'/'sanity.json').is_file()
    assert captured['provenance']['revision'] == 'a'*40
    assert '--revision' in captured['provenance']['execution']['command']


def test_mutable_revision_fails_before_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(reference, 'load_inputs', lambda *_: ({}, {}))
    args = reference.parser().parse_args(['--subset','subset','--selection','selection','--scripts-dir',str(tmp_path),
        '--model','model','--revision','main','--out',str(tmp_path/'out')])
    with pytest.raises(ValueError, match='immutable'):
        reference.generate(args)
    assert not (tmp_path/'out').exists()


def test_external_failure_preserves_logs_and_does_not_publish(tmp_path, monkeypatch):
    scripts = tmp_path / 'scripts'; scripts.mkdir()
    fake_script(scripts / 'embed_cosmos.py', fail=True)
    monkeypatch.setattr(reference, 'load_inputs', lambda *_: ({}, {'video_ids':['a'],'text_ids':['q'], 'clips': media_clips(tmp_path)[:1]}))
    args = reference.parser().parse_args(['--subset','subset','--selection','selection','--scripts-dir',str(scripts),
        '--model','model','--revision','b'*40,'--out',str(tmp_path/'out')])
    with pytest.raises(RuntimeError, match='External script failed'):
        reference.generate(args)
    assert not (tmp_path/'out').exists()
    assert list(tmp_path.glob('.out-reference-*/original/stderr.log'))


def test_unsupported_interface_fails_closed(tmp_path):
    script=tmp_path/'embed_cosmos.py'
    script.write_text('import argparse; argparse.ArgumentParser().parse_args()')
    with pytest.raises(ValueError, match='no advertised revision'):
        reference.external_command(sys.executable, script, {'revision':'a'*40}, {'revision'})


def test_cli_defaults_and_requirements():
    with pytest.raises(SystemExit):
        reference.parser().parse_args([])
    args=reference.parser().parse_args(['--subset','s','--selection','i','--scripts-dir','d','--model','m','--revision','r','--out','o'])
    assert (args.num_frames,args.video_batch,args.text_batch,args.log_level)==(8,2,64,'INFO')


def media_clips(tmp_path):
    clips = []
    for name in ('a', 'b'):
        path = tmp_path / f'{name}.mp4'
        path.write_bytes(name.encode())
        clips.append({'dataset_id': name, 'video_path': str(path), 'media_sha256': sha256_file(path)})
    return clips


def prepared_args(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    media_clips(data)
    (data / 'manifest.json').write_text(json.dumps({'clips': [
        {'chunk_id': name, 'path': f'{name}.mp4'} for name in ('a', 'b')]}))
    (data / 'gt').mkdir()
    (data / 'gt/queries_gt.json').write_text(json.dumps({'queries': [
        {'query': 'query', 'query_domain': 'event', 'relevant_clip_ids': ['a']}]}))
    (tmp_path / 'list.txt').write_text('a.mp4\nb.mp4\n')
    prepare(data, tmp_path / 'list.txt', tmp_path / 'prepared')
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    fake_script(scripts / 'embed_cosmos.py')
    return reference.parser().parse_args([
        '--subset', str(tmp_path / 'prepared/subset.json'),
        '--selection', str(tmp_path / 'prepared/selection.json'),
        '--scripts-dir', str(scripts), '--model', 'model', '--revision', 'a'*40,
        '--out', str(tmp_path / 'out')])


@pytest.mark.parametrize('missing', [False, True])
def test_changed_media_fails_before_any_external_process(tmp_path, monkeypatch, missing):
    args = prepared_args(tmp_path)
    media = tmp_path / 'data/a.mp4'
    if missing:
        media.unlink()
    else:
        media.write_bytes(b'changed')
    launched = []
    monkeypatch.setattr(reference.subprocess, 'run', lambda *a, **k: launched.append(a))
    with pytest.raises(ValueError, match='media'):
        reference.generate(args)
    assert not launched
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('change', ['media', 'helper'])
def test_changed_inputs_during_execution_prevent_publish(tmp_path, monkeypatch, change):
    args = prepared_args(tmp_path)
    helper = tmp_path / 'scripts/preprocessing.py'
    helper.write_text('version = 1')
    original = reference.run_external
    def run_and_change(command, output):
        result = original(command, output)
        if change == 'media':
            (tmp_path / 'data/a.mp4').write_bytes(b'changed')
        else:
            helper.write_text('version = 2')
        return result
    monkeypatch.setattr(reference, 'run_external', run_and_change)
    with pytest.raises(ValueError, match='media|scripts'):
        reference.generate(args)
    assert not (tmp_path / 'out').exists()
    assert list(tmp_path.glob('.out-reference-*/original/execution.json'))


def test_script_hashes_include_support_files_and_ignore_caches(tmp_path):
    (tmp_path / 'embed_cosmos.py').write_text('pass')
    (tmp_path / 'config.json').write_text('{}')
    (tmp_path / 'helpers').mkdir()
    (tmp_path / 'helpers/preprocess.py').write_text('pass')
    for cache in ('__pycache__', '.pytest_cache', '.git'):
        (tmp_path / cache).mkdir()
        (tmp_path / cache / 'cached').write_text('ignore')
    (tmp_path / 'compiled.pyc').write_bytes(b'ignore')
    hashes = reference.script_hashes(tmp_path)
    assert list(hashes) == ['config.json', 'embed_cosmos.py', 'helpers/preprocess.py']
    (tmp_path / 'helpers/preprocess.py').write_text('changed')
    assert reference.script_hashes(tmp_path) != hashes


@pytest.mark.parametrize('output', ['child', 'same', 'parent'])
def test_output_scripts_overlap_rejected_before_launch_or_writes(tmp_path, monkeypatch, output):
    args = prepared_args(tmp_path)
    scripts = Path(args.scripts_dir)
    args.out = str({'child': scripts / 'result', 'same': scripts, 'parent': scripts.parent}[output])
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    def forbidden(*_args, **_kwargs):
        raise AssertionError('overlapping paths must be rejected before hashing media or executing scripts')
    monkeypatch.setattr(reference, 'validate_media', forbidden)
    monkeypatch.setattr(reference.subprocess, 'run', forbidden)
    with pytest.raises(ValueError, match='must not overlap'):
        reference.generate(args)
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    assert before == after
    assert not list(tmp_path.rglob('.*-reference-*'))

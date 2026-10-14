#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run the standalone embedding comparison stages with dependency-aware resume."""
import argparse
import hashlib
import json
import logging
import os
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
REQUIRED = ('data', 'video_list', 'es_index', 'rt_embed_model', 'reference_model', 'reference_revision', 'scripts_dir')
PATH_KEYS = ('data', 'video_list', 'scripts_dir', 'deployment_config', 'auth_config', 'ca_bundle', 'python')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--log-level', default='INFO')
    return p


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2)
        f.write('\n')
        temp = f.name
    os.replace(temp, path)


def load_config(path):
    path = Path(path).resolve()
    c = json.loads(path.read_text())
    if not isinstance(c, dict):
        raise ValueError('Run configuration must be a JSON object')
    for key in REQUIRED:
        if not isinstance(c.get(key), str) or not c[key]:
            raise ValueError(f'Missing required configuration field: {key}')
    if not re.fullmatch(r'[0-9a-fA-F]{40}', c['reference_revision']):
        raise ValueError('reference_revision must be an immutable 40-character Hugging Face commit hash')
    for key in ('num_frames', 'video_batch', 'text_batch', 'complete_retries'):
        if key in c and (isinstance(c[key], bool) or not isinstance(c[key], int) or c[key] < 1):
            raise ValueError(f'{key} must be a positive integer')
    for key in ('complete_backoff', 'wait_timeout', 'poll_interval'):
        if key in c and (isinstance(c[key], bool) or not isinstance(c[key], (int, float)) or c[key] <= 0):
            raise ValueError(f'{key} must be positive')
    for key in PATH_KEYS:
        if c.get(key):
            # Bare Python executable names are resolved by subprocess/PATH.
            if key == 'python' and '/' not in c[key]:
                continue
            c[key] = str((path.parent / c[key]).resolve())
    if not Path(c['data']).is_dir():
        raise ValueError('data must be an existing dataset directory')
    if not Path(c['video_list']).is_file():
        raise ValueError('video_list must be an existing file')
    if not Path(c['scripts_dir']).is_dir():
        raise ValueError('scripts_dir must be an existing directory')
    if not (Path(c['scripts_dir']) / 'embed_cosmos.py').is_file():
        raise ValueError('scripts_dir must contain the original embed_cosmos.py')
    if len(list(Path(c['scripts_dir']).glob('*.py'))) < 3:
        raise ValueError('scripts_dir must contain all three original Drive scripts')
    for key in ('deployment_config', 'auth_config', 'ca_bundle'):
        if c.get(key) and not Path(c[key]).is_file():
            raise ValueError(f'{key} must be an existing file')
    return c


def artifact_hashes(directory):
    return {str(p.relative_to(directory)): sha(p) for p in sorted(Path(directory).rglob('*')) if p.is_file()}


def reusable(record, fingerprint, directory):
    return (record.get('status') == 'complete' and record.get('dependency_fingerprint') == fingerprint
            and bool(record.get('artifacts')) and record['artifacts'] == artifact_hashes(directory))


def commands(c, out, deployment):
    py = c.get('python', sys.executable)
    selection = out / 'subset' / 'selection.json'
    subset = out / 'subset' / 'subset.json'
    net = ['--deployment-config', str(deployment)]
    for key in ('auth_config', 'ca_bundle'):
        if c.get(key):
            net += ['--' + key.replace('_', '-'), c[key]]
    def cmd(name, args):
        return [py, str(ROOT / (name + '.py')), *map(str, args), '--log-level', c.get('log_level', 'INFO')]
    return [
        ('subset', cmd('prepare_subset', ['--data', c['data'], '--video-list', c['video_list'], '--out', out / 'subset'])),
        ('ingestion', cmd('ingest_subset', ['--selection', selection, '--out', out / 'ingestion', '--upload-timestamp', c.get('upload_timestamp', '2025-01-01T00:00:00'), '--complete-retries', c.get('complete_retries', 3), '--complete-backoff', c.get('complete_backoff', 5), *net])),
        ('vss', cmd('collect_vss_embeddings', ['--subset', subset, '--selection', selection, '--ingestion', out / 'ingestion' / 'ingestion.json', '--es-index', c['es_index'], '--model', c['rt_embed_model'], '--out', out / 'vss', '--wait-timeout', c.get('wait_timeout', 1200), '--poll-interval', c.get('poll_interval', 10), '--vector-field', c.get('vector_field', 'llm.visionEmbeddings.vector'), '--sensor-field', c.get('sensor_field', 'sensor.id.keyword'), *net])),
        ('reference', cmd('generate_reference_embeddings', ['--subset', subset, '--selection', selection, '--scripts-dir', c['scripts_dir'], '--model', c['reference_model'], '--revision', c['reference_revision'], '--out', out / 'reference', '--python', py, '--num-frames', c.get('num_frames', 8), '--video-batch', c.get('video_batch', 2), '--text-batch', c.get('text_batch', 64)])),
        ('report', cmd('evaluate_and_report', ['--subset', subset, '--vss-dir', out / 'vss', '--reference-dir', out / 'reference', '--scripts-dir', c['scripts_dir'], '--out', out / 'report', '--python', py])),
    ]


def stage_inputs(c, inputs, name):
    implementations = inputs['implementation']
    files = {'subset': 'prepare_subset.py', 'ingestion': 'ingest_subset.py',
             'vss': 'collect_vss_embeddings.py', 'reference': 'generate_reference_embeddings.py',
             'report': 'evaluate_and_report.py'}
    value = {'implementation': implementations.get(files[name]),
             'common': implementations.get('comparison_common.py'), 'python': c.get('python', sys.executable)}
    if name == 'subset':
        value.update(dataset=inputs['dataset'], video_list=inputs['video_list'])
    if name in ('ingestion', 'vss'):
        value.update(deployment=inputs['deployment'], auth=inputs.get('auth_config'), ca=inputs.get('ca_bundle'))
    if name == 'ingestion':
        value['upload_timestamp'] = c.get('upload_timestamp', '2025-01-01T00:00:00')
    if name == 'vss':
        value.update(model=c['rt_embed_model'], es_index=c['es_index'],
                     vector_field=c.get('vector_field', 'llm.visionEmbeddings.vector'),
                     sensor_field=c.get('sensor_field', 'sensor.id.keyword'))
    if name == 'reference':
        value.update(model=c['reference_model'], revision=c['reference_revision'],
                     num_frames=c.get('num_frames', 8), video_batch=c.get('video_batch', 2),
                     text_batch=c.get('text_batch', 64),
                     scripts=inputs['scripts'])
    if name == 'report':
        value['scripts'] = {key: val for key, val in inputs['scripts'].items() if key != 'embed_cosmos.py'}
        value['adapter'] = implementations.get('generate_reference_embeddings.py')
    return value


def preflight_external(c):
    from generate_reference_embeddings import ALIASES, advertised_flags
    from evaluate_and_report import discover_script, discover_event_script
    py = c.get('python', sys.executable)
    flags = advertised_flags(py, Path(c['scripts_dir']) / 'embed_cosmos.py')
    for key in ('subset', 'out', 'model', 'revision', 'num_frames', 'video_batch', 'text_batch'):
        if not any(flag in flags for flag in ALIASES[key]):
            raise ValueError(f'Original embed_cosmos.py has no supported {key} option')
    scorer = discover_script(c['scripts_dir'], py, ('subset', 'text', 'video', 'out'), ('embed_cosmos.py',))
    discover_event_script(c['scripts_dir'], py, ('embed_cosmos.py', scorer.name))


def run(args):
    from comparison_common import load_deployment, deployment_document, preflight, script_hashes
    c = load_config(args.config)
    out = args.out.resolve()
    dataset = Path(c['data'])
    if out == dataset or dataset in out.parents or out in dataset.parents:
        raise ValueError('--out and the dataset directory must not overlap; use a separate run directory')
    scripts_dir = Path(c['scripts_dir'])
    if out == scripts_dir or scripts_dir in out.parents or out in scripts_dir.parents:
        raise ValueError('--out and the Drive scripts directory must not overlap; use a separate run directory')
    if out.exists() and not out.is_dir():
        raise ValueError('--out must be a directory')
    if not args.dry_run and not args.resume and out.exists() and any(out.iterdir()):
        raise ValueError('--out is nonempty; use --resume or a fresh --out directory')
    # Hash all local dataset files: media changes must invalidate subset even before
    # a previous selection manifest exists. This is deliberately exhaustive.
    inputs = {'config': c, 'dataset': artifact_hashes(Path(c['data'])),
              'video_list': sha(c['video_list']), 'scripts': script_hashes(Path(c['scripts_dir'])),
              'implementation': {p.name: sha(p) for p in ROOT.glob('*.py')}}
    for key in ('auth_config', 'ca_bundle'):
        if c.get(key):
            inputs[key] = sha(c[key])
    deployment_doc = deployment_document(c.get('deployment_config'))
    inputs['deployment'] = deployment_doc
    base = digest(inputs)
    if args.dry_run:
        preflight_external(c)
        # Exercise the real subset validator without publishing run artifacts.
        with tempfile.TemporaryDirectory(prefix='embedding-comparison-dry-') as tmp:
            cmd = commands(c, Path(tmp), Path(tmp) / 'deployment.json')[0][1]
            subprocess.run(cmd, check=True)
        logging.info('Local configuration and subset validation passed; no network calls made')
        return
    manifest_path = out / 'run.json'
    manifest = json.loads(manifest_path.read_text()) if args.resume and manifest_path.exists() else {'schema_version': 1, 'stages': {}}
    # Check recovery compatibility before publishing any replacement inputs or state.
    # A rebuilt subset gets a new run ID even when selected media is unchanged.
    ingestion_dir = out / 'ingestion'
    if args.resume and ingestion_dir.exists() and any(ingestion_dir.iterdir()):
        subset_record = manifest['stages'].get('subset', {})
        subset_fingerprint = digest({'inputs': stage_inputs(c, inputs, 'subset'), 'dependencies': {}, 'stage': 'subset'})
        if not reusable(subset_record, subset_fingerprint, out / 'subset'):
            raise ValueError('Subset dependencies or artifacts changed; use a fresh --out directory to preserve upload recovery state')
        ingestion_dependencies = {'subset': {'artifacts': subset_record['artifacts'], 'dependency_fingerprint': subset_record['dependency_fingerprint']}}
        ingestion_fingerprint = digest({'inputs': stage_inputs(c, inputs, 'ingestion'), 'dependencies': ingestion_dependencies, 'stage': 'ingestion'})
        if manifest['stages'].get('ingestion', {}).get('dependency_fingerprint') != ingestion_fingerprint:
            raise ValueError('Ingestion dependencies changed; use a fresh --out directory to preserve upload recovery state')
    out.mkdir(parents=True, exist_ok=True)
    deployment_path = out / '.deployment.json'
    write(deployment_path, deployment_doc)
    os.chmod(deployment_path, 0o600)
    deployment = load_deployment(deployment_path)
    manifest['configuration_fingerprint'] = base
    dependencies = {'subset': [], 'ingestion': ['subset'], 'vss': ['subset', 'ingestion'], 'reference': ['subset'], 'report': ['subset', 'vss', 'reference']}
    write(manifest_path, manifest)
    try:
        preflight_external(c)
        preflight(deployment, c.get('auth_config'), c.get('ca_bundle'), es_index=c['es_index'], model=c['rt_embed_model'])
        manifest.pop('preflight_error', None)
    except Exception as exc:
        manifest['preflight_error'] = type(exc).__name__
        write(manifest_path, manifest)
        raise
    for name, cmd in commands(c, out, deployment_path):
        deps = {d: {'artifacts': manifest['stages'][d]['artifacts'], 'dependency_fingerprint': manifest['stages'][d]['dependency_fingerprint']} for d in dependencies[name]}
        fingerprint = digest({'inputs': stage_inputs(c, inputs, name), 'dependencies': deps, 'stage': name})
        previous = manifest['stages'].get(name, {})
        if args.resume and reusable(previous, fingerprint, out / name):
            logging.info('Reusing complete stage %s', name)
            continue
        if args.resume and name in ('ingestion', 'vss') and previous.get('dependency_fingerprint') == fingerprint:
            cmd.append('--resume')
        elif name == 'ingestion' and (out / name).exists() and any((out / name).iterdir()):
            # Never overwrite recovery state from a different dataset/config.
            raise ValueError(f'{name} dependencies changed or --resume is absent; use a fresh --out directory to preserve upload recovery state')
        if name in ('vss', 'reference', 'report') and not (name == 'vss' and args.resume and previous.get('dependency_fingerprint') == fingerprint) and (out / name).exists() and any((out / name).iterdir()):
            archive = out / '.superseded' / f'{name}-{time.time_ns()}'
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(out / name), archive)
        record = {'status': 'running', 'dependency_fingerprint': fingerprint, 'dependencies': dependencies[name], 'started_at': time.time()}
        manifest['stages'][name] = record
        write(manifest_path, manifest)
        try:
            subprocess.run(cmd, check=True)
            record.update(status='complete', artifacts=artifact_hashes(out / name))
            if name == 'subset':
                selection_data = json.loads((out / name / 'selection.json').read_text())
                manifest['run_id'] = selection_data.get('run_id')
                manifest['subset_fingerprint'] = selection_data.get('subset_fingerprint')
        except Exception as exc:
            record.update(status='failed', error=type(exc).__name__)
            raise
        finally:
            record['elapsed_seconds'] = time.time() - record['started_at']
            write(manifest_path, manifest)


def main():
    args = parser().parse_args()
    logging.basicConfig(level=args.log_level)
    try:
        run(args)
    except Exception as exc:
        logging.error('Comparison failed: %s', exc)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Execute external, pinned Cosmos embedding code and publish a validated bundle."""
import argparse
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import numpy as np
from comparison_common import load_inputs, read_json, script_hashes, sha256_file, write_bundle, write_json

# The original Drive files are external. Verify their advertised interface before
# invocation; unsupported interfaces fail explicitly rather than guessing flags.
ALIASES = {
    'subset': ('--subset', '--data', '--dataset', '--dataset-json', '--dataset_json'),
    'out': ('--out', '--output-dir', '--output_dir', '--out-dir', '--out_dir'),
    'model': ('--model', '--model-id', '--model_id', '--model-name', '--model_name'),
    'revision': ('--revision', '--model-revision', '--model_revision'),
    'num_frames': ('--num-frames', '--num_frames'),
    'video_batch': ('--video-batch', '--video_batch', '--video-batch-size', '--video_batch_size'),
    'text_batch': ('--text-batch', '--text_batch', '--text-batch-size', '--text_batch_size'),
    'text': ('--text', '--text-emb', '--text-embeddings', '--text_embeddings', '--text-npy', '--text_npy'),
    'video': ('--video', '--video-emb', '--video-embeddings', '--video_embeddings', '--video-npy', '--video_npy'),
    'video_ids': ('--video-ids', '--video_ids'),
    'emb_dir': ('--emb-dir', '--emb_dir'),
    'metrics': ('--metrics', '--metrics-json', '--metrics_json', '--results'),
}


def advertised_flags(python, script):
    result = subprocess.run([python, str(script), '--help'], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f'{script.name} --help failed: {result.stderr[-2000:]}')
    return set(re.findall(r'--[A-Za-z][A-Za-z0-9_-]*', result.stdout + result.stderr))


def external_command(python, script, values, required):
    flags = advertised_flags(python, script)
    command = [python, str(script)]
    for key, value in values.items():
        flag = next((f for f in ALIASES[key] if f in flags), None)
        if flag:
            command.extend([flag, str(value)])
        elif key in required:
            raise ValueError(f'{script.name} has no advertised {key} option; supported spellings: {ALIASES[key]}')
    return command


def run_external(command, output):
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with (output / 'stdout.log').open('w') as stdout, (output / 'stderr.log').open('w') as stderr:
        result = subprocess.run(command, stdout=stdout, stderr=stderr)
    record = {'command': command, 'returncode': result.returncode, 'elapsed_seconds': time.monotonic() - started}
    write_json(output / 'execution.json', record)
    if result.returncode:
        raise RuntimeError(f'External script failed ({result.returncode}); see {output / "stderr.log"}')
    return record


def environment_versions(python):
    # Ask the selected interpreter, which may differ from this process.
    code = """import importlib.metadata,json,sys
versions={}
for name in ['numpy','torch','transformers','decord','opencv-python']:
    try: versions[name]=importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError: pass
print(json.dumps({'python':sys.version,'packages':versions}))
"""
    result = subprocess.run([python, '-c', code], capture_output=True, text=True)
    return json.loads(result.stdout) if result.returncode == 0 else {'unavailable': True}


def validate_media(selection):
    """Ensure reference preprocessing reads the media recorded at preparation."""
    for clip in selection['clips']:
        path = Path(clip['video_path'])
        try:
            actual = sha256_file(path)
        except OSError as exc:
            raise ValueError(f"Selected media cannot be read: {clip['dataset_id']} ({path}); prepare subset again") from exc
        if actual != clip['media_sha256']:
            raise ValueError(f"Selected media hash mismatch: {clip['dataset_id']} ({path}); prepare subset again")


def generate(args):
    subset, selection = load_inputs(args.subset, args.selection)
    if not re.fullmatch(r'[0-9a-fA-F]{40}', args.revision):
        raise ValueError('--revision must be an immutable 40-character Hugging Face commit hash')
    if min(args.num_frames, args.video_batch, args.text_batch) < 1:
        raise ValueError('Frame and batch counts must be positive')
    script = Path(args.scripts_dir).resolve() / 'embed_cosmos.py'
    if not script.is_file():
        raise ValueError(f'Missing external Drive script: {script}')
    out = Path(args.out).resolve()
    scripts_dir = script.parent
    if out == scripts_dir or scripts_dir in out.parents or out in scripts_dir.parents:
        raise ValueError('--out and --scripts-dir must not overlap; choose an output directory outside the scripts directory and its ancestors')
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Output directory must be empty: {out}; interrupted reference generation restarts in a new directory')
    validate_media(selection)
    scripts_before = script_hashes(script.parent)
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f'.{out.name}-reference-', dir=out.parent))
    raw = temporary / 'original'
    try:
        values = {'subset': Path(args.subset).resolve(), 'out': raw, 'model': args.model,
                  'revision': args.revision, 'num_frames': args.num_frames,
                  'video_batch': args.video_batch, 'text_batch': args.text_batch}
        command = external_command(args.python, script, values, set(values))
        record = run_external(command, raw)
        text = np.load(raw / 'text.npy', allow_pickle=False)
        video = np.load(raw / 'video.npy', allow_pickle=False)
        # Original scripts must write IDs: without them gallery order cannot be
        # verified, even when row counts happen to agree.
        ids = read_json(raw / 'video_ids.json')
        wanted = selection['video_ids']
        if len(ids) != len(set(ids)) or set(ids) != set(wanted):
            raise ValueError('Original video IDs must exactly match the selected canonical IDs')
        video = video[[ids.index(identity) for identity in wanted]]
        if (raw / 'text_ids.json').exists():
            text_ids = read_json(raw / 'text_ids.json')
            if len(text_ids) != len(set(text_ids)) or set(text_ids) != set(selection['text_ids']):
                raise ValueError('Original text IDs do not match subset query identities')
            text = text[[text_ids.index(identity) for identity in selection['text_ids']]]
        provenance = {'approach': 'reference', 'model': args.model, 'revision': args.revision,
                      'script': {'path': str(script), 'sha256': scripts_before['embed_cosmos.py']},
                      'script_hashes': scripts_before,
                      'execution': record, 'environment': environment_versions(args.python),
                      'num_frames': args.num_frames, 'video_batch': args.video_batch, 'text_batch': args.text_batch,
                      'text_order': 'original subset order' if not (raw / 'text_ids.json').exists() else 'identity aligned'}
        validate_media(selection)
        if script_hashes(script.parent) != scripts_before:
            raise ValueError('External reference scripts or support resources changed during execution; restart reference generation')
        write_bundle(temporary, subset, selection, text, video, provenance)
        if out.exists():
            out.rmdir()
        os.replace(temporary, out)
    except BaseException:
        logging.error('Reference generation recovery logs retained at %s', temporary)
        raise


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('subset', 'selection', 'scripts-dir', 'model', 'revision', 'out'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--python', default=sys.executable)
    p.add_argument('--num-frames', type=int, default=8)
    p.add_argument('--video-batch', type=int, default=2)
    p.add_argument('--text-batch', type=int, default=64)
    p.add_argument('--log-level', default='INFO')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=args.log_level)
    try:
        generate(args)
        return 0
    except Exception as exc:
        logging.error('%s', exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())

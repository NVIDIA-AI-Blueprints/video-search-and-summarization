#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Upload an explicit selection with durable three-step recovery checkpoints."""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import re
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import quote

from comparison_common import Network, digest, load_deployment, metadata, network_args, read_json, sha256_file, write_json, validate_selection

# Kept identical to flows.ingest.classify_complete_failure, without importing
# flows' orchestration-heavy package initializer into the standalone entrypoint.
COMPLETE_ALREADY_REGISTERED = 'already-registered'
COMPLETE_FATAL = 'fatal'


def classify_complete_failure(status_code, body):
    if 'duplicate camera id' in (body or '').lower():
        return COMPLETE_ALREADY_REGISTERED
    if status_code >= 500 or status_code == 429:
        return 'retry'
    return COMPLETE_FATAL


def public_body(value):
    """Remove credentials and URL handles from the exported ingestion report."""
    if isinstance(value, dict):
        return {k: public_body(v) for k, v in value.items() if not any(x in k.lower() for x in ("token", "authorization", "secret", "password", "url"))}
    if isinstance(value, list):
        return [public_body(v) for v in value]
    if isinstance(value, str) and re.search(r"https?://", value):
        return "[redacted URL]"
    return value


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--selection', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--upload-timestamp', default='2025-01-01T00:00:00')
    p.add_argument('--complete-retries', type=int, default=3)
    p.add_argument('--complete-backoff', type=float, default=5)
    p.add_argument('--resume', action='store_true')
    network_args(p)
    return p


def run(args, network=None):
    if args.complete_retries < 1 or args.complete_backoff < 0:
        raise ValueError('Completion retries must be positive and backoff nonnegative')
    selection = read_json(args.selection)
    validate_selection(selection)
    config = load_deployment(args.deployment_config)
    dep = digest({'selection': selection, 'deployment': config, 'timestamp': args.upload_timestamp,
                  'auth_config': sha256_file(args.auth_config) if args.auth_config else None,
                  'ca_bundle': sha256_file(args.ca_bundle) if args.ca_bundle else None})
    target = Path(args.out) / 'ingestion.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    private_path = target.parent / 'ingestion.private.json'
    private = read_json(private_path) if private_path.exists() else {}
    if target.exists():
        if not args.resume:
            raise ValueError('ingestion.json exists; use --resume to preserve uploads')
        state = read_json(target)
        if state.get('dependency_fingerprint') != dep:
            raise ValueError('Ingestion dependencies changed; use a new output directory')
    else:
        state = metadata(selection, dependency_fingerprint=dep, clips=[], complete=False)
    net = network or Network(config, args.auth_config, args.ca_bundle)
    net.preflight(['agent'])
    clips = selection['clips']
    by_id = {r['dataset_id']: r for r in state['clips']}
    if len(by_id) != len(state['clips']) or set(by_id) - set(selection['video_ids']):
        raise ValueError('Duplicate or unrelated ingestion identities')
    for clip in clips:
        cid = clip['dataset_id']
        path = Path(clip['video_path'])
        expected_hash = clip['media_sha256']
        if sha256_file(path) != expected_hash:
            raise ValueError(f'Media changed for {cid}')
        row = by_id.get(cid)
        if row is not None and any(row.get(k) != clip[k] for k in ('dataset_id', 'filename', 'media_sha256')):
            raise ValueError(f'Ingestion media identity changed for {cid}')
        if row is None:
            row = {k: clip[k] for k in ('dataset_id', 'filename', 'media_sha256')}
            row.update(phase='new', attempts={'acquire': 0, 'upload': 0, 'complete': 0}, timings={})
            state['clips'].append(row)
        saved = private.get(str(cid), {})
        if not row.get('sensor_id') and saved.get('upload_identifier') == row.get('upload_identifier') and saved.get('completion_body', {}).get('sensorId'):
            row['sensor_id'] = str(saved['completion_body']['sensorId'])
            row['completion_body'] = public_body(saved['completion_body'])
            row['phase'] = 'uploaded'
            write_json(target, state)
        if row.get('phase') == 'complete':
            continue
        if row.get('phase') in ('uploading', 'uncertain') and not row.get('sensor_id'):
            raise RuntimeError(f'Upload {row.get("upload_identifier")} for {cid} is uncertain; reconcile its sensor ID before resuming')
        started = time.monotonic()
        try:
            if not row.get('sensor_id'):
                row['attempts']['acquire'] += 1
                write_json(target, state)
                t0 = time.monotonic()
                response = net.request('agent', 'POST', '/v1/videos', json={'filename': row['filename']}, timeout=30)
                response.raise_for_status()
                url = response.json().get('url')
                if not isinstance(url, str) or not url:
                    raise RuntimeError('Agent returned no upload URL')
                row['timings']['acquire_seconds'] = time.monotonic() - t0
                row['upload_identifier'] = str(uuid.uuid4())
                row['phase'] = 'uploading'
                row['attempts']['upload'] += 1
                write_json(target, state)  # durable before sending any bytes; URL deliberately omitted
                t0 = time.monotonic()
                with path.open('rb') as handle:
                    response = net.absolute_upload(url, method='POST', headers={
                        'nvstreamer-chunk-number': '1', 'nvstreamer-total-chunks': '1',
                        'nvstreamer-is-last-chunk': 'true', 'nvstreamer-identifier': row['upload_identifier'],
                        'nvstreamer-file-name': row['filename']},
                        files={'mediaFile': (row['filename'], handle, mimetypes.guess_type(path.name)[0] or 'video/mp4')},
                        data={'filename': row['filename'], 'metadata': json.dumps({'timestamp': args.upload_timestamp})}, timeout=900)
                response.raise_for_status()
                body = response.json() or {}
                if not body.get('sensorId'):
                    raise RuntimeError('Upload returned no sensorId; reconciliation required')
                row['sensor_id'] = str(body['sensorId'])
                complete_body = {**body, 'filename': row['filename']}
                private[str(cid)] = {'upload_identifier': row['upload_identifier'], 'completion_body': complete_body}
                write_json(private_path, private)
                os.chmod(private_path, 0o600)
                row['completion_body'] = public_body(complete_body)
                row['phase'] = 'uploaded'
                row['timings']['upload_seconds'] = time.monotonic() - t0
                write_json(target, state)
            t0 = time.monotonic()
            for attempt in range(1, args.complete_retries + 1):
                row['phase'] = 'completing'
                row['attempts']['complete'] += 1
                write_json(target, state)
                try:
                    response = net.request('agent', 'POST', '/v1/videos/' + quote(row['sensor_id'], safe='') + '/complete',
                                           json=private.get(str(cid), {}).get('completion_body', row['completion_body']), timeout=900)
                except Exception:
                    if attempt == args.complete_retries:
                        raise RuntimeError('Completion transport failed; resume with recorded sensor ID') from None
                    time.sleep(args.complete_backoff * attempt)
                    continue
                if response.ok:
                    completion = response.json() or {}
                    chunks = completion.get('chunks_processed')
                    if not isinstance(chunks, int) or isinstance(chunks, bool) or chunks <= 0:
                        raise RuntimeError(f'Completion reported chunks_processed={chunks!r}')
                    row.update(completion=public_body(completion), chunks_processed=chunks, complete_outcome='acknowledged')
                    break
                verdict = classify_complete_failure(response.status_code, response.text)
                if verdict == COMPLETE_ALREADY_REGISTERED:
                    row.update(completion={}, chunks_processed=None, complete_outcome='already-registered-provisional')
                    break
                if verdict == COMPLETE_FATAL or attempt == args.complete_retries:
                    raise RuntimeError(f'Completion failed with HTTP {response.status_code}; resume retains sensor ID')
                time.sleep(args.complete_backoff * attempt)
            row['timings']['complete_seconds'] = time.monotonic() - t0
            row['phase'] = 'complete'
            row.pop('error', None)
        except Exception as exc:
            if row['phase'] == 'uploading':
                row['phase'] = 'uncertain'
            # Exception text can contain signed URLs and credentials; preserve safe classification only.
            detail = str(exc) if isinstance(exc, RuntimeError) and str(exc).startswith(('Completion', 'Agent returned', 'Upload returned')) else 'Stage failed; inspect phase and reconcile uncertain uploads before retrying'
            row['error'] = {'type': type(exc).__name__, 'message': detail}
            write_json(target, state)
            raise RuntimeError(f'Ingestion failed for {cid} at phase {row["phase"]}: {detail}') from None
        finally:
            row['timings']['last_attempt_seconds'] = time.monotonic() - started
            write_json(target, state)
    state['complete'] = len(state['clips']) == len(clips) and all(r.get('phase') == 'complete' and r.get('sensor_id') for r in state['clips'])
    write_json(target, state)
    return target


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper()))
    try:
        run(args)
        return 0
    except Exception as exc:
        logging.error('%s', exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())

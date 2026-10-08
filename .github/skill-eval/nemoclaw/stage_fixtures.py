# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Stage spec-declared media through NemoClaw's upload transport."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

SOURCE = Path('/tmp/vss-sample-data/dev-profile-sample-data')
DESTINATION = '/tmp/vss-sample-data/dev-profile-sample-data'
REPORT = Path('/logs/artifacts/nemoclaw/fixtures.json')
SCAN_REPORT = Path('/logs/artifacts/nemoclaw/host-fixture.json')


def validate_files(files):
    if not isinstance(files, list) or not files or any(
        not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.mp4', name)
        for name in files
    ) or len(files) != len(set(files)):
        raise ValueError('fixtures must be distinct MP4 basenames')
    return files


def call(args, *, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("sandbox fixture staging deadline exceeded")
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=remaining).stdout


def stage(sandbox, files, source=SOURCE, *, report=None):
    files = validate_files(files)
    deadline = time.monotonic() + 30 + 300 * len(files)
    # Check every host input before uploading anything. Downloads remain the
    # setup workflow's job; this helper copies only the declared media files.
    if report is not None:
        report['stage'] = 'host_fixtures'
    inputs = []
    for name in files:
        path = source / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'missing or empty host fixture: {name}')
        inputs.append((name, path, hashlib.sha256(path.read_bytes()).hexdigest()))
    if report is not None:
        report['stage'] = 'fixture_directory'
    call(['openshell', 'sandbox', 'exec', '-n', sandbox, '--', 'mkdir', '-p', DESTINATION], deadline=deadline)
    rows = []
    for name, path, digest in inputs:
        target = f'{DESTINATION}/{name}'
        if report is not None:
            report.update(stage='fixture_upload', file=name)
        call(['nemoclaw', sandbox, 'upload', str(path), target], deadline=deadline)
        if report is not None:
            report['stage'] = 'fixture_checksum'
        remote = call(['openshell', 'sandbox', 'exec', '-n', sandbox, '--', 'sha256sum', target], deadline=deadline).split()
        if not remote or remote[0] != digest:
            raise ValueError(f'sandbox fixture checksum mismatch: {name}')
        rows.append({'file': name, 'bytes': path.stat().st_size, 'sha256': digest, 'status': 'verified'})
    return rows


def stage_nvstreamer_scan(sandbox, name, source=SOURCE, *, report):
    """Prepare the host filesystem precondition; the sandbox tests scan APIs."""
    validate_files([name])
    path = source / name
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f'missing or empty host fixture: {name}')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    deadline = time.monotonic() + 180
    report['stage'] = 'nvstreamer_container'
    container = 'vss-vios-nvstreamer'
    info = json.loads(call(['docker', 'inspect', container], deadline=deadline))[0]
    service = (info.get('Config', {}).get('Labels') or {}).get('com.docker.compose.service', '')
    container_id = info.get('Id', '')
    if (
        not info.get('State', {}).get('Running')
        or not service.startswith('nvstreamer')
        or not re.fullmatch(r'[a-f0-9]{64}', container_id)
    ):
        raise ValueError('expected a running VSS Compose NvStreamer container')
    destinations = [
        m['Destination'] for m in info.get('Mounts', [])
        if m.get('Type') == 'bind' and m.get('RW') is True
        and isinstance(m.get('Destination'), str)
        and m['Destination'].endswith('/streamer_videos')
    ]
    if len(destinations) != 1 or not Path(destinations[0]).is_absolute() or '..' in Path(destinations[0]).parts:
        raise ValueError('NvStreamer videos bind mount is missing or ambiguous')
    basename = 'sample-clip-' + uuid.uuid4().hex[:12]
    target = f'{destinations[0]}/{basename}.mp4'
    report['stage'] = 'nvstreamer_copy'
    call(['docker', 'cp', str(path), f'{container_id}:{target}'], deadline=deadline)
    checksum = call(['docker', 'exec', container_id, 'sha256sum', target], deadline=deadline).split()
    if not checksum or checksum[0] != digest:
        raise ValueError('NvStreamer fixture checksum mismatch')
    metadata = {'kind': 'nvstreamer_scan', 'basename': basename, 'source': name,
                'container': container, 'container_path': target, 'sha256': digest}
    report.update(metadata, stage='fixture_manifest')
    with tempfile.TemporaryDirectory(prefix='nvstreamer-fixture-') as directory:
        manifest = Path(directory) / 'nvstreamer_scan.json'
        manifest.write_text(json.dumps(metadata) + '\n')
        target_manifest = DESTINATION + '/nvstreamer_scan.json'
        call(['nemoclaw', sandbox, 'upload', str(manifest), target_manifest], deadline=deadline)
        remote = call(['openshell', 'sandbox', 'exec', '-n', sandbox, '--', 'sha256sum', target_manifest], deadline=deadline).split()
        if not remote or remote[0] != hashlib.sha256(manifest.read_bytes()).hexdigest():
            raise ValueError('sandbox fixture manifest checksum mismatch')
    return metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sandbox', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--files-json')
    mode.add_argument('--nvstreamer-scan-file')
    args = parser.parse_args()
    report = {'sandbox': args.sandbox, 'status': 'failed', 'files': []}
    report_path = SCAN_REPORT if args.nvstreamer_scan_file else REPORT
    try:
        if args.nvstreamer_scan_file:
            report.update(stage_nvstreamer_scan(args.sandbox, args.nvstreamer_scan_file, report=report))
            report.update(status='passed', stage='complete')
        else:
            files = json.loads(args.files_json)
            if files == []:
                report['status'] = 'not_required'
            else:
                report['files'] = stage(args.sandbox, files, report=report)
                report['status'] = 'passed'
                report['stage'] = 'complete'
                report.pop('file', None)
    except Exception as exc:
        # No CLI stdout/stderr or credentials enter the artifact.
        report['error_type'] = type(exc).__name__
        raise
    finally:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()

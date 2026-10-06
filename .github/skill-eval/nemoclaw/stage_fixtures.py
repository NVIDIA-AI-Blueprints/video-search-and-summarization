# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Stage spec-declared media through NemoClaw's upload transport."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


SOURCE = Path('/tmp/vss-sample-data/dev-profile-sample-data')
DESTINATION = '/tmp/vss-sample-data/dev-profile-sample-data'
REPORT = Path('/logs/artifacts/nemoclaw/fixtures.json')


def validate_files(files):
    if not isinstance(files, list) or not files or any(
        not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.mp4', name)
        for name in files
    ) or len(files) != len(set(files)):
        raise ValueError('fixtures must be distinct MP4 basenames')
    return files


def call(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=45).stdout


def stage(sandbox, files, source=SOURCE):
    files = validate_files(files)
    # Check every host input before uploading anything. Downloads remain the
    # setup workflow's job; this helper copies only the declared media files.
    inputs = []
    for name in files:
        path = source / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'missing or empty host fixture: {name}')
        inputs.append((name, path, hashlib.sha256(path.read_bytes()).hexdigest()))
    call(['openshell', 'sandbox', 'exec', '-n', sandbox, '--', 'mkdir', '-p', DESTINATION])
    rows = []
    for name, path, digest in inputs:
        target = f'{DESTINATION}/{name}'
        call(['nemoclaw', sandbox, 'upload', str(path), target])
        remote = call(['openshell', 'sandbox', 'exec', '-n', sandbox, '--', 'sha256sum', target]).split()
        if not remote or remote[0] != digest:
            raise ValueError(f'sandbox fixture checksum mismatch: {name}')
        rows.append({'file': name, 'bytes': path.stat().st_size, 'sha256': digest, 'status': 'verified'})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sandbox', required=True)
    parser.add_argument('--files-json', required=True)
    args = parser.parse_args()
    report = {'sandbox': args.sandbox, 'status': 'failed', 'files': []}
    try:
        report['files'] = stage(args.sandbox, json.loads(args.files_json))
        report['status'] = 'passed'
    except Exception as exc:
        # No CLI stdout/stderr or credentials enter the artifact.
        report['error_type'] = type(exc).__name__
        raise
    finally:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()

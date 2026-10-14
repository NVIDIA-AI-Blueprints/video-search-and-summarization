# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Versioned, file-backed evidence. No network dependency; summaries are derived."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
from datetime import datetime, timezone

VERSION = 1


class IntegrityError(RuntimeError):
    """Evidence changed; never recover by repeating inference."""



def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with tmp.open('xb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode())


def read_json(path):
    return json.loads(Path(path).read_bytes())


def documents(stdout):
    """Decode concatenated JSON/NDJSON, including multiline envelopes and banners."""
    decoder = json.JSONDecoder()
    output = []
    pos = 0
    while pos < len(stdout):
        if stdout[pos].isspace():
            pos += 1
            continue
        try:
            value, end = decoder.raw_decode(stdout, pos)
        except json.JSONDecodeError:
            # Skip a non-JSON banner line, never search inside a corrupt document.
            end = stdout.find('\n', pos)
            if end == -1:
                break
            pos = end + 1
        else:
            output.append(value)
            pos = end
    return output


def response(stdout):
    docs = documents(stdout)
    envelopes = [d for d in docs if isinstance(d, dict) and 'data' in d]
    if len(envelopes) != 1 or not isinstance(envelopes[0]['data'], list):
        raise ValueError('Expected exactly one complete search envelope with list-valued data')
    if not all(isinstance(h, dict) for h in envelopes[0]['data']):
        raise ValueError('Search hits must be objects')
    return {'documents': docs, 'envelope': envelopes[0]}


def seal(directory, names, **metadata):
    directory = Path(directory)
    atomic_json(directory / 'complete.json', {
        'version': VERSION, **metadata,
        'sha256': {name: digest(directory / name) for name in names},
    })


def verify(directory):
    directory = Path(directory)
    record = read_json(directory / 'complete.json')
    if record['version'] != VERSION:
        raise ValueError('Unsupported capture version')
    for name, expected in record['sha256'].items():
        if digest(directory / name) != expected:
            raise IntegrityError(f'Capture integrity failure: {directory / name}')
    return record


def recover(directory):
    """Reparse saved successful process output; never infer success from partial output."""
    directory = Path(directory)
    if (directory / 'complete.json').exists():
        verify(directory)
    else:
        process = read_json(directory / 'process.json')
        if process['returncode'] != 0 or process.get('error'):
            raise ValueError('Process did not finish successfully')
        for name, expected in process['sha256'].items():
            if digest(directory / name) != expected:
                raise IntegrityError(f'Output integrity failure: {name}')
        try:
            parsed = response((directory / 'stdout.bin').read_text())
        except (ValueError, UnicodeError) as exc:
            atomic_json(directory / 'parse-error.json', {'type': type(exc).__name__, 'error': str(exc)})
            raise
        atomic_json(directory / 'response.json', parsed)
        seal(directory, ['request.json', 'stdout.bin', 'stderr.bin', 'process.json', 'response.json'], status='success')
    return read_json(directory / 'response.json'), read_json(directory / 'process.json')


def run(directory, argv, *, cwd, timeout, metadata):
    """Write intent before exec, stream bytes to disk, seal only parsed successes."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    atomic_json(directory / 'request.json', {
        'version': VERSION, **metadata, 'argv': argv, 'cwd': cwd,
        'attempt_id': directory.name, 'started_at': now(), 'timeout_s': timeout,
    })
    started = time.monotonic()
    code = None
    error = None
    try:
        with (directory / 'stdout.bin').open('xb') as out, (directory / 'stderr.bin').open('xb') as err:
            try:
                proc = subprocess.run(argv, stdout=out, stderr=err, cwd=cwd, timeout=timeout, check=False)
                code = proc.returncode
            finally:
                out.flush()
                err.flush()
                os.fsync(out.fileno())
                os.fsync(err.fileno())
    except BaseException as exc:
        error = {'type': type(exc).__name__, 'message': str(exc),
                 'uncertain_execution': True, 'timeout': isinstance(exc, subprocess.TimeoutExpired),
                 'cancelled': isinstance(exc, (KeyboardInterrupt, SystemExit))}
        raise
    finally:
        atomic_json(directory / 'process.json', {
            'returncode': code, 'ended_at': now(), 'elapsed_s': time.monotonic() - started,
            'error': error, 'sha256': {name: digest(directory / name)
                for name in ('request.json', 'stdout.bin', 'stderr.bin') if (directory / name).exists()},
        })
    return subprocess.CompletedProcess(argv, code,
        (directory / 'stdout.bin').read_text(), (directory / 'stderr.bin').read_text()), read_json(directory / 'process.json')['elapsed_s']

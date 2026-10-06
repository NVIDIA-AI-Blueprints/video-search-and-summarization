# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Missing or corrupted sandbox media prevents setup from passing."""

import hashlib
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location('fixture_staging', Path(__file__).resolve().parents[1] / 'nemoclaw/stage_fixtures.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


@pytest.mark.parametrize('bad', [[], ['../secret.mp4'], ['credentials.env'], ['a.mp4', 'a.mp4'], 'a.mp4'])
def test_rejects_non_media_or_ambiguous_declarations(bad):
    with pytest.raises(ValueError):
        fixtures.validate_files(bad)


def test_uploads_only_declared_media_and_checks_hash(monkeypatch, tmp_path):
    (tmp_path / 'warehouse_safety_0001.mp4').write_bytes(b'video')
    (tmp_path / 'credentials.env').write_text('secret')
    digest = hashlib.sha256(b'video').hexdigest()
    calls = []
    def call(args):
        calls.append(args)
        return digest + '  file\n' if 'sha256sum' in args else ''
    monkeypatch.setattr(fixtures, 'call', call)
    rows = fixtures.stage('se-current', ['warehouse_safety_0001.mp4'], tmp_path)
    assert rows[0]['status'] == 'verified'
    assert rows[0]['sha256'] == digest
    assert calls[1] == ['nemoclaw', 'se-current', 'upload', str(tmp_path / 'warehouse_safety_0001.mp4'), fixtures.DESTINATION + '/warehouse_safety_0001.mp4']
    assert all('secret' not in str(args) for args in calls)


def test_missing_host_fixture_never_uploads(monkeypatch, tmp_path):
    monkeypatch.setattr(fixtures, 'call', lambda args: pytest.fail('must validate before transport'))
    with pytest.raises(ValueError, match='missing or empty'):
        fixtures.stage('se-current', ['missing.mp4'], tmp_path)


def test_checksum_mismatch_fails(monkeypatch, tmp_path):
    (tmp_path / 'a.mp4').write_bytes(b'video')
    monkeypatch.setattr(fixtures, 'call', lambda args: 'incorrect  file\n' if 'sha256sum' in args else '')
    with pytest.raises(ValueError, match='checksum mismatch'):
        fixtures.stage('se-current', ['a.mp4'], tmp_path)


def test_multiple_files_are_verified_individually(monkeypatch, tmp_path):
    names = ['warehouse_sample.mp4', 'sample-warehouse-ladder.mp4']
    for name in names:
        (tmp_path / name).write_bytes(name.encode())
    calls = []
    def call(args):
        calls.append(args)
        if 'sha256sum' in args:
            name = Path(args[-1]).name
            return hashlib.sha256(name.encode()).hexdigest() + '  file\n'
        return ''
    monkeypatch.setattr(fixtures, 'call', call)
    rows = fixtures.stage('se-current', names, tmp_path)
    assert [row['file'] for row in rows] == names
    assert all(row['status'] == 'verified' for row in rows)
    assert len(calls) == 5

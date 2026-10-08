# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ingest_subset as subject
from comparison_common import sha256_file, write_json, read_json


class Response:
    def __init__(self, body=None, status=200, text=''):
        self.body, self.status_code, self.text = body or {}, status, text
        self.ok = status < 400
    def json(self):
        return self.body
    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError('HTTP error')


class Network:
    def __init__(self, out, fail_upload=False, fail_complete=False):
        self.out, self.fail_upload, self.fail_complete = out, fail_upload, fail_complete
        self.uploads = 0
        self.calls = []
    def preflight(self, services):
        self.calls.append(('preflight', services))
    def request(self, service, method, path, **kwargs):
        self.calls.append((service, method, path, kwargs))
        if path == '/v1/videos':
            return Response({'url': 'https://returned.example/prefix?token=private'})
        persisted = read_json(self.out / 'ingestion.json')['clips'][0]
        assert persisted['sensor_id'] == 'sensor/1'
        assert persisted['completion_body']['filename'] == 'a.mp4'
        if self.fail_complete:
            return Response(status=503)
        return Response({'chunks_processed': 1})
    def absolute_upload(self, url, **kwargs):
        self.uploads += 1
        row = read_json(self.out / 'ingestion.json')['clips'][0]
        assert row['phase'] == 'uploading' and row['upload_identifier']
        assert url == 'https://returned.example/prefix?token=private'
        assert kwargs['headers']['nvstreamer-identifier'] == row['upload_identifier']
        if self.fail_upload:
            raise TimeoutError('private upload URL')
        return Response({'sensorId': 'sensor/1', 'url': 'https://private.example/?token=secret'})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    video = tmp_path / 'a.mp4'
    video.write_bytes(b'video')
    selection = tmp_path / 'selection.json'
    write_json(selection, {'schema_version': 1, 'run_id': 'run', 'subset_fingerprint': 'fingerprint',
                          'video_ids': ['clip'], 'text_ids': ['query'], 'queries': [{'query_id': 'query'}],
                          'clips': [{'dataset_id': 'clip', 'filename': video.name, 'video_path': str(video),
                                     'media_sha256': sha256_file(video)}]})
    out = tmp_path / 'out'
    args = subject.parser().parse_args(['--selection', str(selection), '--out', str(out), '--complete-backoff', '0'])
    monkeypatch.setattr(subject, 'load_deployment', lambda _: {'agent': 'https://host/api'})
    return args, out


def test_checkpoint_and_resume_completion(setup):
    args, out = setup
    net = Network(out, fail_complete=True)
    with pytest.raises(RuntimeError):
        subject.run(args, net)
    assert net.uploads == 1
    args.resume = True
    net.fail_complete = False
    subject.run(args, net)
    subject.run(args, net)
    assert net.uploads == 1
    state = read_json(out / 'ingestion.json')
    assert state['complete'] and state['clips'][0]['phase'] == 'complete'
    assert 'https://' not in json.dumps(state)
    assert (out / 'ingestion.private.json').stat().st_mode & 0o777 == 0o600


def test_uncertain_upload_never_reuploads(setup):
    args, out = setup
    net = Network(out, fail_upload=True)
    with pytest.raises(RuntimeError):
        subject.run(args, net)
    args.resume = True
    net.fail_upload = False
    with pytest.raises(RuntimeError, match='reconcile'):
        subject.run(args, net)
    assert net.uploads == 1


def test_provisional_duplicate_completion(setup):
    args, out = setup
    net = Network(out)
    original = net.request
    net.request = lambda service, method, path, **kwargs: Response(status=500, text='Duplicate Camera id') if path.endswith('/complete') else original(service, method, path, **kwargs)
    subject.run(args, net)
    row = read_json(out / 'ingestion.json')['clips'][0]
    assert row['complete_outcome'] == 'already-registered-provisional'
    assert row['chunks_processed'] is None


def test_dependency_changes_refused(setup):
    args, out = setup
    net = Network(out)
    subject.run(args, net)
    args.resume = True
    args.upload_timestamp = 'different'
    with pytest.raises(ValueError, match='dependencies changed'):
        subject.run(args, net)
    assert net.uploads == 1


def test_cli_defaults():
    args = subject.parser().parse_args(['--selection', 'selection.json', '--out', 'out'])
    assert (args.upload_timestamp, args.complete_retries, args.complete_backoff, args.resume) == ('2025-01-01T00:00:00', 3, 5, False)
    with pytest.raises(SystemExit):
        subject.parser().parse_args([])


def test_private_sensor_checkpoint_reconciles_crash(setup):
    args, out = setup
    net = Network(out, fail_complete=True)
    with pytest.raises(RuntimeError):
        subject.run(args, net)
    state = read_json(out / 'ingestion.json')
    row = state['clips'][0]
    row.pop('sensor_id')
    row.pop('completion_body')
    row['phase'] = 'uploading'
    write_json(out / 'ingestion.json', state)
    args.resume = True
    net.fail_complete = False
    subject.run(args, net)
    assert net.uploads == 1
    assert read_json(out / 'ingestion.json')['clips'][0]['sensor_id'] == 'sensor/1'


def test_duplicate_selection_rejected_before_network(setup):
    args, out = setup
    selection = read_json(args.selection)
    selection['video_ids'].append('clip')
    selection['clips'].append(selection['clips'][0])
    write_json(args.selection, selection)
    net = Network(out)
    with pytest.raises(ValueError, match='duplicate'):
        subject.run(args, net)
    assert not net.calls

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import copy
import json
import pytest
from vss_perf_reporting.identity import enriched_config, finalize_directory, full_image_ref
from vss_perf_reporting import build_result

REF = 'nvcr.io/nvidia/vss-core/vss-rt-vlm:nightly-TEST'
ENV = {'PERF_ARTIFACT_REF': REF, 'PERF_HARDWARE_PLATFORM': 'IGX-T7000', 'PERF_IMAGE_ARCH': 'arm64-igpu'}


@pytest.mark.parametrize('ref', ['tag-only', 'repo/image', 'https://host/image:tag', 'host/user:secret@repo:tag', 'host/repo:tag\n', 'host/repo@sha256:bad'])
def test_reject_ambiguous_or_unsafe_refs(ref):
    assert not full_image_ref(ref)


def test_additive_identity_never_mutates_config(monkeypatch):
    config = {'vlm': 'model', 'observed_image_ref': 'docker-observed-value'}
    original = copy.deepcopy(config)
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    result = build_result('RTVI-VLM', 'run', [], config=config)
    assert config == original
    actual = result['metadata']['config']
    assert actual['artifact_ref'] == REF
    assert actual['hardware_platform'] == 'IGX-THOR'
    assert actual['artifact_identity_source'] == 'orchestrator_resolved_input'
    assert actual['observed_image_ref'] == config['observed_image_ref']
    assert 'digest' not in actual


@pytest.mark.parametrize('service', ['LVS', 'RTVI-VLM', 'RTVI-Embed', 'Behavior-Analytics', 'Video-Analytics-API', 'Alerts-Verification'])
def test_finalize_preserves_all_measurements_and_failures(tmp_path, service):
    directory = tmp_path / 'vss-perf-results'
    directory.mkdir()
    report = build_result(service, 'run', [{'test_case_id': 'case', 'status': 'FAIL', 'metrics': {'latency': {'p99_s': 0}}}], passed=0, failed=1)
    path = directory / 'report.json'
    path.write_text(json.dumps(report))
    native = tmp_path / 'diagnostics.json'; native.write_text('{"keep":true}')
    assert finalize_directory(tmp_path, ENV) == 1
    actual = json.loads(path.read_text())
    assert actual['test_cases'] == report['test_cases']
    assert actual['summary'] == report['summary']
    assert actual['metadata']['run_info'] == report['metadata']['run_info']
    assert native.read_text() == '{"keep":true}'
    assert finalize_directory(tmp_path, ENV) == 1


def test_conflicting_identity_prevents_any_rewrite(tmp_path):
    directory = tmp_path / 'vss-perf-results'; directory.mkdir()
    good = directory / 'a.json'
    bad = directory / 'z.json'
    report = build_result('LVS', 'run', [])
    good.write_text(json.dumps(report)); original = good.read_bytes()
    report['metadata']['config']['artifact_ref'] = 'host/repo:different'
    bad.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='contradicts'):
        finalize_directory(tmp_path, ENV)
    assert good.read_bytes() == original


def test_profile_revision_changes_identity_but_failure_does_not(tmp_path):
    setup = tmp_path / 'perf/configs'; setup.mkdir(parents=True)
    profiles = setup.parent / 'benchmark'; profiles.mkdir()
    profile = profiles / 'profile.yaml'; profile.write_text('streams: 1\n')
    env = dict(ENV, PERF_IDENTITY_SELECTION='{"configId":"rtvi","scenario":"quick_test"}', PERF_IDENTITY_CONFIG_DIR=str(setup))
    success = enriched_config({'benchmark_profile': 'benchmark/profile.yaml'}, env)
    failure = enriched_config({'error': 'deployment failed'}, env)
    assert success['configuration_fingerprint'] == failure['configuration_fingerprint']
    profile.write_text('streams: 2\n')
    assert enriched_config({}, env)['configuration_fingerprint'] != failure['configuration_fingerprint']

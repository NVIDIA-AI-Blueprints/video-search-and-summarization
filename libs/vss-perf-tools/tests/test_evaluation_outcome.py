# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation_outcome import latency_kpi_failure


def payload_for(key):
    violation = 'frames.corrected.p50_s 0.1 > 0.02' if key == 'topics' else 'frames.p50_s 0.1 > 0.05'
    measured = {'count': 20, 'p50_s': .1, 'p99_s': .15}
    return {'success': False, 'raw': {'count': 1000},
            key: {'frames': {'present': True, **({'corrected': measured} if key == 'topics' else measured)}},
            'kpis': {'passed': False, 'violations': [violation]},
            'error': 'KPI violations: ' + violation}


@pytest.mark.parametrize('key', ['topics', 'endpoints'])
def test_completed_latency_failure_is_nonfatal(key):
    payload = payload_for(key)
    before = deepcopy(payload)
    assert latency_kpi_failure(payload, key, 1)
    assert payload == before


@pytest.mark.parametrize('key', ['topics', 'endpoints'])
@pytest.mark.parametrize('code', [-15, 2, 130, 137, 143])
def test_interruption_and_cleanup_failure_remain_fatal(key, code):
    assert not latency_kpi_failure(payload_for(key), key, code)


@pytest.mark.parametrize('violation', ['frames missing', 'frames.ok_count 0 < 10',
    'frames.corrected.count 0 < 1000', 'frames.ok_ratio 0.5 < 1.0',
    'frames.p50_s missing', 'frames.p50_s nan > 0.05', 'no KPI spec for quick_test/unknown'])
def test_data_and_configuration_errors_remain_fatal(violation):
    payload = payload_for('endpoints')
    payload['kpis']['violations'] = [violation]
    payload['error'] = 'KPI violations: ' + violation
    assert not latency_kpi_failure(payload, 'endpoints')


@pytest.mark.parametrize('key', ['topics', 'endpoints'])
def test_missing_or_invalid_measurement_is_not_hidden(key):
    payload = payload_for(key)
    payload[key]['frames']['present'] = False
    assert not latency_kpi_failure(payload, key)
    payload[key] = {}
    assert not latency_kpi_failure(payload, key)
    payload[key] = {'frames': 'invalid'}
    assert not latency_kpi_failure(payload, key)


def test_unrelated_stage_or_error_is_not_hidden():
    payload = payload_for('endpoints')
    payload['failure_stage'] = 'teardown'
    assert not latency_kpi_failure(payload, 'endpoints')
    del payload['failure_stage']
    payload['error'] = 'deployment failed'
    assert not latency_kpi_failure(payload, 'endpoints')

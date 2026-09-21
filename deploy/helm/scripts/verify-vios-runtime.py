#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render VIOS timeouts and base-profile endpoint defaults. Build dependencies first."""
import json
import subprocess
from pathlib import Path

import yaml

root = Path(__file__).resolve().parents[1]
stream = root / 'services/vios/charts/vios-streamprocessing'
base = root / 'developer-profiles/dev-profile-base'


def render(chart, values):
    return subprocess.run(['helm', 'template', 'review', str(chart), '-f', '-'],
                          input=yaml.safe_dump({'enabled': True, **values}), text=True, capture_output=True)


for value in (None, 120, 1800, 2147483647):
    result = render(stream, {} if value is None else {'downloadFilesTimeoutSecs': value})
    assert result.returncode == 0, result.stderr
    configs = [d for d in yaml.safe_load_all(result.stdout) if d and d['kind'] == 'ConfigMap']
    config = next(d for d in configs if 'vst_config.json' in d.get('data', {}))
    assert json.loads(config['data']['vst_config.json'])['data']['download_files_timeout_secs'] == (value or 120)
for value in (None, 0, -1, True, 1.5, 1800.9, 'abc', 2147483648):
    result = render(stream, {'downloadFilesTimeoutSecs': value})
    assert result.returncode != 0 and 'downloadFilesTimeoutSecs' in result.stderr, value
for prefixed in (False, True):
    result = render(base, {'ngc': {'createSecrets': False}, 'global': {'useReleaseNamePrefix': prefixed}})
    assert result.returncode == 0, result.stderr
    sensor = next(d for d in yaml.safe_load_all(result.stdout)
                  if d and d['kind'] == 'Deployment' and d['metadata']['name'].endswith('vss-vios-sensor'))
    env = {e['name']: e.get('value') for e in sensor['spec']['template']['spec']['containers'][0]['env']}
    expected = 'http://' + ('review-' if prefixed else '') + 'vss-vios-streamprocessing:30001'
    assert env['STREAM_PROCESSOR_MODULE_ENDPOINT'] == env['RTSP_SERVER_MODULE_ENDPOINT'] == expected
for annotations in ({}, {'traefik.ingress.kubernetes.io/router.middlewares': 'example-routes@kubernetescrd'},
                    {'haproxy.org/path-rewrite': '/custom /(.*)'}):
    result = render(base, {'ngc': {'createSecrets': False},
                          'global': {'externalHost': 'example.test', 'useReleaseNamePrefix': True},
                          'rtvi': {'vss-rtvi-vlm': {'enabled': True}},
                          'vssIngress': {'enabled': True, 'ingressClassName': 'traefik', 'annotations': annotations}})
    assert result.returncode == 0, result.stderr
    ingress = next(d for d in yaml.safe_load_all(result.stdout) if d and d['kind'] == 'Ingress')
    assert ingress['spec']['ingressClassName'] == 'traefik'
    actual = ingress['metadata']['annotations']
    assert all(actual.get(k) == v for k, v in annotations.items())
    assert 'haproxy.org/path-rewrite' in actual
    services = {p['path']: p['backend']['service']['name']
                for p in ingress['spec']['rules'][0]['http']['paths']}
    assert services['/vst'] == 'review-vss-vios-ingress'
    assert services['/rtvi-vlm'] == 'review-vss-rtvi-vlm'
print('VIOS timeout, direct/prefixed endpoints, and ingress annotation render checks passed')

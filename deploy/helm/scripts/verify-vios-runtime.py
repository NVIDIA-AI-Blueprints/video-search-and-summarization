#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render VIOS timeouts and base-profile endpoint defaults. Build dependencies first."""
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

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


def sensor_env(documents):
    sensor = next(d for d in documents
                  if d['kind'] == 'Deployment' and d['metadata']['name'].endswith('vss-vios-sensor'))
    return {e['name']: e.get('value') for e in sensor['spec']['template']['spec']['containers'][0]['env']}


def assert_peers_rendered(documents, env, names):
    """Every peer address the sensor is given names a Service this same render creates."""
    service_names = {d['metadata']['name'] for d in documents if d['kind'] == 'Service'}
    for name in names:
        assert urlsplit(env[name]).hostname in service_names, (name, env[name], sorted(service_names))


# A mixed-prefix install: global true, stream processing (and the sensor) local false. The
# sensor cannot read the stream-processing chart's local flag, so the install states it in
# peerUseReleaseNamePrefix; an explicit false must survive the global true (coalesce would
# read it as unset and name a Service that is never rendered).
result = render(base, {'ngc': {'createSecrets': False}, 'global': {'useReleaseNamePrefix': True},
                       'vios': {'vss-vios-sensor': {'useReleaseNamePrefix': False,
                                                    'peerUseReleaseNamePrefix': {'streamprocessing': False}},
                                'vss-vios-streamprocessing': {'useReleaseNamePrefix': False}}})
assert result.returncode == 0, result.stderr
documents = [d for d in yaml.safe_load_all(result.stdout) if d]
env = sensor_env(documents)
assert env['STREAM_PROCESSOR_MODULE_ENDPOINT'] == env['RTSP_SERVER_MODULE_ENDPOINT'] == \
    'http://vss-vios-streamprocessing:30001', env['STREAM_PROCESSOR_MODULE_ENDPOINT']
# The ingress keeps the global prefix, so the sensor must still address it by its real name.
assert env['VST_INGRESS_ENDPOINT'] == 'http://review-vss-vios-ingress:30888/vst', env['VST_INGRESS_ENDPOINT']
assert_peers_rendered(documents, env, ('STREAM_PROCESSOR_MODULE_ENDPOINT', 'RTSP_SERVER_MODULE_ENDPOINT',
                                       'VST_INGRESS_ENDPOINT'))

# The sensor's OWN local flag names the sensor, not its peers. Alerts routes the sensor
# through SDRC (VST_USE_SDRC=true); SDRC is rendered by infra's sdrc chart and follows the
# global prefix, so a sensor-local false must not strip the prefix from the SDRC address.
alerts = root / 'developer-profiles/dev-profile-alerts'
for sdrc_local, expected_host in ((None, 'review-sdrc-controller'), (False, 'sdrc-controller')):
    values = {'ngc': {'createSecrets': False}, 'global': {'useReleaseNamePrefix': True},
              'vios': {'vss-vios-sensor': {'useReleaseNamePrefix': False}}}
    if sdrc_local is not None:
        # SDRC's own local flag is out of the sensor's sight: state it for the peer too.
        values['infra'] = {'sdrc': {'useReleaseNamePrefix': sdrc_local}}
        values['vios']['vss-vios-sensor']['peerUseReleaseNamePrefix'] = {'sdrc': sdrc_local}
    result = render(alerts, values)
    assert result.returncode == 0, result.stderr
    documents = [d for d in yaml.safe_load_all(result.stdout) if d]
    env = sensor_env(documents)
    assert env.get('VST_USE_SDRC') == 'true', env.get('VST_USE_SDRC')
    assert env['STREAM_PROCESSOR_MODULE_ENDPOINT'] == env['RTSP_SERVER_MODULE_ENDPOINT'] == \
        f'http://{expected_host}:10000', (sdrc_local, env['STREAM_PROCESSOR_MODULE_ENDPOINT'])
    assert_peers_rendered(documents, env, ('STREAM_PROCESSOR_MODULE_ENDPOINT', 'RTSP_SERVER_MODULE_ENDPOINT'))
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
    rewrites = actual['haproxy.org/path-rewrite']
    if 'haproxy.org/path-rewrite' in annotations:
        # An explicit rewrite replaces the generated table rather than merging into it.
        assert rewrites == annotations['haproxy.org/path-rewrite'], rewrites
    else:
        # Keep the literal-block trailing newline so existing HAProxy Ingresses see no diff.
        assert rewrites.endswith('\n'), repr(rewrites[-20:])
        assert '/rtvi-vlm/(.*) /\\1' in rewrites and '^/storage /vst/storage' in rewrites, rewrites
    services = {p['path']: p['backend']['service']['name']
                for p in ingress['spec']['rules'][0]['http']['paths']}
    assert services['/vst'] == 'review-vss-vios-ingress'
    assert services['/rtvi-vlm'] == 'review-vss-rtvi-vlm'
print('VIOS timeout, direct/SDRC/mixed-prefix peer endpoints, and ingress annotation render checks passed')

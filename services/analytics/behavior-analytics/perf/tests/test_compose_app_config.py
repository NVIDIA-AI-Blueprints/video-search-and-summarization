# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resolve real Compose files offline to catch infrastructure-root regressions."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

PERF = Path(__file__).resolve().parents[1]
INTEGRATION = PERF.parent / 'tests/integration'


def test_app_overlay_keeps_infrastructure_and_build_paths(tmp_path):
    if not shutil.which('docker'):
        pytest.skip('Docker Compose CLI required')
    env = {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME')}
    env.update(SCRIPT_DIR=str(INTEGRATION), ENV_FILE=str(tmp_path/'infra.env'),
               PROFILE1='warehouse_2d', PROFILE2='kafka',
               BA_PERF_DATA_DIR=str(tmp_path/'runtime'),
               BEHAVIOR_ANALYTICS_IMAGE='example/behavior-analytics:test',
               PERF_SCRIPT=str(INTEGRATION/'generate_env.sh'),
               PERF_OVERLAY=str(PERF/'setup/compose-app-config.yml'))
    result = subprocess.run(['bash','-c','''set -euo pipefail
set -a
source "$PERF_SCRIPT" >/dev/null
set +a
docker compose --project-directory "$SCRIPT_DIR/docker_compose" --env-file "$ENV_FILE" \
 -f "$SCRIPT_DIR/docker_compose/infra/compose.yml" \
 -f "$SCRIPT_DIR/docker_compose/apps/mdx-apps.yml" \
 -f "$PERF_OVERLAY" config --format json
'''], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)['services']
    root = tmp_path/'runtime/apps/warehouse_2d'
    for name in ('analytics-stream', 'analytics-stream-playback'):
        mounts={item['target']:item['source'] for item in services[name]['volumes']}
        assert mounts['/resources/analytics-stream-config.json'] == str(root/'analytics-stream-kafka-config.json')
    assert (INTEGRATION/'docker_compose/infra/build-network.yml').is_file()
    assert services['elasticsearch']['build']['network'] == 'default'
    assert Path(services['elasticsearch']['build']['context']).is_dir()

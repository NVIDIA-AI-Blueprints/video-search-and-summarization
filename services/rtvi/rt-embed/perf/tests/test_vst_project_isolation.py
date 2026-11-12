# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise the staged package's real Bash commands with a fake Docker CLI."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

PERF = Path(__file__).resolve().parents[1]
HELPER = PERF / 'scripts/isolate_vst_projects.py'
spec = importlib.util.spec_from_file_location('isolate_vst_projects', HELPER)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize('project_option', ['-p shared', '--project-name=shared', '-p "$COMPOSE_PROJECT_NAME"', ''])
def test_vst_restart_preserves_nvstreamer_and_teardown_is_scoped(tmp_path, project_option):
    deploy = tmp_path / 'deploy.sh'
    deploy.write_text('#!/usr/bin/env bash\nset -eu\nCOMPOSE_PROJECT_NAME=shared\n'
                      'docker compose ' + project_option + ' -f "$2.yaml" --env-file "config file.env" "$1" --remove-orphans\n')
    module.isolate(deploy)
    staged = deploy.read_bytes()
    module.isolate(deploy)
    assert deploy.read_bytes() == staged
    docker = tmp_path / 'docker'
    docker.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ['FAKE_STORE'])
s = json.loads(p.read_text())
a = sys.argv[1:]
assert a[:2] == ['compose', '--project-name']
project = a[2]
assert a[3:7] == ['-f', os.environ['COMPONENT'] + '.yaml', '--env-file', 'config file.env']
assert a[-1] == '--remove-orphans'
if a[-2] == 'down': s.pop(project, None)
elif a[-2] == 'up': s[project] = os.environ['COMPONENT']
else: raise AssertionError(a)
p.write_text(json.dumps(s))
''')
    docker.chmod(0o755)
    store = tmp_path / 'state.json'
    store.write_text(json.dumps({'unrelated-monitoring': 'keep'}))
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'], FAKE_STORE=str(store))

    def run(action, component):
        return subprocess.run(['bash', str(deploy), action, component],
                              env=dict(env, COMPONENT=component, PERF_VST_COMPONENT_PROJECT='run-123-' + component),
                              check=True, capture_output=True, text=True)

    run('up', 'nvstreamer')
    run('down', 'vst')
    assert json.loads(store.read_text())['run-123-nvstreamer'] == 'nvstreamer'
    run('up', 'vst')
    run('down', 'vst')
    run('up', 'vst')
    assert json.loads(store.read_text()) == {'unrelated-monitoring': 'keep', 'run-123-nvstreamer': 'nvstreamer', 'run-123-vst': 'vst'}
    run('down', 'vst')
    run('down', 'nvstreamer')
    assert json.loads(store.read_text()) == {'unrelated-monitoring': 'keep'}
    missing = subprocess.run(['bash', str(deploy), 'up', 'vst'],
                             env={k: v for k, v in env.items() if k != 'PERF_VST_COMPONENT_PROJECT'},
                             capture_output=True, text=True)
    assert missing.returncode != 0
    assert json.loads(store.read_text()) == {'unrelated-monitoring': 'keep'}


def test_all_setup_and_teardown_deploy_calls_select_component_project():
    for name in ('native_setup_perf_env.sh', 'native_teardown_perf_env.sh'):
        lines = (PERF / 'scripts' / name).read_text().splitlines()
        for i, line in enumerate(lines):
            if 'bash deploy.sh' not in line:
                continue
            context = '\n'.join(lines[max(0, i - 6):i + 1])
            component = 'nvstreamer' if 'nvstreamer' in line else 'vst'
            assert 'PERF_VST_COMPONENT_PROJECT="${VST_COMPOSE_PROJECT}-' + component + '"' in context
    for name in ('native_setup_perf_env.sh', 'run_benchmark.sh'):
        text = (PERF / 'scripts' / name).read_text()
        assert '--compose-project "${VST_COMPOSE_PROJECT}-vst"' in text
        assert '--compose-project "${VST_COMPOSE_PROJECT}-nvstreamer"' in text


def test_unsupported_deploy_script_is_not_modified(tmp_path):
    p = tmp_path / 'deploy.sh'
    for original in ('#!/bin/bash\nsudo docker compose up\n', '#!/bin/bash\necho no compose\n'):
        p.write_text(original)
        with pytest.raises(ValueError):
            module.isolate(p)
        assert p.read_text() == original

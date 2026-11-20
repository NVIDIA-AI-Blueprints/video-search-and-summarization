# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'perf/scripts/run_benchmark.sh'


def parse(tmp_path, args, environment=None, resolve_default=False):
    source = RUNNER.read_text()
    block = source[source.index('COMPOSE_FILE=""'):source.index('shift $((OPTIND - 1))')]
    if resolve_default:
        auto = source[source.index('if [ -z "$BENCHMARK_CONFIG" ]; then'):source.index('# Resolve benchmark config:')]
        block += '\nCOMPOSE_FILE_PATH="$COMPOSE_FILE"\n' + auto
    command = '''set -euo pipefail
error() { echo "$*" >&2; }
warn() { :; }
usage() { exit 0; }
''' + block + '''
printf '%s\\0' "$COMPOSE_FILE" "$BENCHMARK_CONFIG" "$OUTPUT_JSON_NAME" "$COMMON_OUTPUT_DIR" "$TEARDOWN" "$HEALTH_TIMEOUT" "$IMAGE_TAG_OVERRIDE" "$VLM_GPUS" "$LLM_GPUS" "${CONFIG_ID:-}" "${TRIGGERED_BY:-}" "${PIPELINE_URL:-}" "${SCENARIO_ARGS[@]}"
'''
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path), **(environment or {})}
    return subprocess.run(['bash','-c',command,'runner',*args], cwd=tmp_path,
                          env=env, text=True, capture_output=True)


def test_common_cli_translates_without_changing_workload(tmp_path):
    result = parse(tmp_path, ['--compose-file','test.yml','--config','auto','--scenario','one,two',
        '--output-json',str(tmp_path/'reports'),'--vlm-gpus','1,2','--llm-gpus','3',
        '--config-id','cfg','--triggered-by','manual','--pipeline-url','https://ci/job/1/'],
        {'IMAGE_TAG':'registry/service:tag','PERF_LVS_HEALTH_TIMEOUT_SECONDS':'1200'})
    assert result.returncode == 0, result.stderr
    values = result.stdout.split('\0')
    assert values[:9] == ['test.yml','','vss-perf-results',str(tmp_path/'reports'),'true','1200','registry/service:tag','1,2','3']
    assert values[9:14] == ['cfg','manual','https://ci/job/1/','one','two']


def test_legacy_short_options_keep_defaults(tmp_path):
    result = parse(tmp_path, ['-f','test.yml','-c','custom.yaml','-s','one','-s','two','-v','0'])
    assert result.returncode == 0, result.stderr
    values = result.stdout.split('\0')
    assert values[:9] == ['test.yml','custom.yaml','','','false','600','','0','']
    assert values[-3:-1] == ['one','two']


@pytest.mark.parametrize('arguments',[['--compose-file'], ['--unknown','value']])
def test_invalid_common_arguments_fail_before_deployment(tmp_path, arguments):
    assert parse(tmp_path,arguments).returncode == 2


def test_image_loading_precedes_common_compose_operations():
    source = RUNNER.read_text()
    load = source.index('eval "$image_environment"')
    validate = source.index('"${COMPOSE_FILE_ARGS[@]}" config --quiet')
    pull = source.index('"${COMPOSE_FILE_ARGS[@]}" pull --include-deps')
    up = source.index('"${COMPOSE_FILE_ARGS[@]}" up -d')
    assert load < validate < pull < up


@pytest.mark.parametrize('compose,expected', [('x86-rtvi.yaml','config.yaml'),('thor-test.yaml','tegra_config.yaml'),('spark-test.yaml','tegra_config.yaml'),('orin-nx-test.yaml','tegra_config.yaml')])
def test_common_auto_config_preserves_platform_defaults(tmp_path,compose,expected):
    result=parse(tmp_path,['--compose-file',compose,'--config','auto','--output-json',str(tmp_path/'out')],resolve_default=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split('\0')[1] == expected


def test_common_root_may_contain_diagnostics_but_native_must_be_empty(tmp_path):
    root = tmp_path / 'reports'
    (root / 'diagnostics').mkdir(parents=True)
    (root / 'diagnostics/entrypoint.log').write_text('current run')
    args = ['--config', 'auto', '--output-json', str(root)]
    assert parse(tmp_path, args).returncode == 0
    native = root / 'native'
    native.mkdir()
    assert parse(tmp_path, args).returncode == 0
    sentinel = native / '.old-result'
    sentinel.write_text('preserve')
    result = parse(tmp_path, args)
    assert result.returncode == 2
    assert 'Native output directory is not empty' in result.stderr
    assert sentinel.read_text() == 'preserve'
    assert (root / 'diagnostics/entrypoint.log').read_text() == 'current run'


@pytest.mark.parametrize('symlink', [False, True])
def test_invalid_native_output_fails_without_modifying_it(tmp_path, symlink):
    root = tmp_path / 'reports'
    root.mkdir()
    target = tmp_path / 'target'
    target.mkdir()
    if symlink:
        (root / 'native').symlink_to(target, target_is_directory=True)
    else:
        (root / 'native').write_text('preserve')
    result = parse(tmp_path, ['--config', 'auto', '--output-json', str(root)])
    assert result.returncode == 2
    assert not list(target.iterdir())


@pytest.mark.parametrize('mode', ['common', 'legacy_ci', 'standalone'])
def test_output_routing_passes_native_empty_directory_guard(tmp_path, mode):
    import ast
    import logging
    import sys

    source = RUNNER.read_text()
    block = source[source.index('if [ -n "$OUTPUT_JSON_NAME" ]; then', source.index('# CI mode:')):
                   source.index('cd "$BENCHMARK_DIR"', source.index('# CI mode:'))]
    root = tmp_path / 'run'
    (root / 'diagnostics').mkdir(parents=True)
    (root / 'diagnostics/entrypoint.log').write_text('streaming')
    (root / 'support-images.json').write_text('{}')
    script = '''set -euo pipefail
step() { :; }
COMMON_OUTPUT_DIR="$1"
OUTPUT_JSON_NAME="$2"
BENCHMARK_ROOT="$3"
COMPOSE_FILE=example.yaml
COMPOSE_REL_PATH=compose/example.yaml
BENCHMARK_CONFIG_PATH=config.yaml
SCENARIO_ARGS=(quick_test)
VLM_GPUS=0
LLM_GPUS=1
RELEASE=3.4.0
''' + block + '''
printf '%s\\0' "$VIA_OUTPUT_DIR" "${BENCHMARK_CMD[@]}"
'''
    result = subprocess.run(['bash', '-c', script, 'runner',
                             str(root) if mode == 'common' else '',
                             '' if mode == 'standalone' else 'vss-perf-results',
                             str(tmp_path)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    values = result.stdout.split('\0')
    output = values[0]
    if mode == 'common':
        assert output == str(root / 'native')
        assert values[values.index('--output-json') + 1] == str(root / 'vss-perf-results')
    elif mode == 'legacy_ci':
        assert output == str(tmp_path / 'vss-perf-report')
        assert values[values.index('--output-json') + 1] == 'vss-perf-results'
    else:
        assert output.startswith(str(tmp_path / 'vss-perf-report-example-'))
    # Execute the actual native guard, rather than duplicating its condition.
    tree = ast.parse((ROOT / 'perf/benchmark/core/vss_perf_benchmark.py').read_text())
    guard = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                 and 'already exists and is not empty' in ast.unparse(node))
    scope = {'os': os, 'sys': sys, 'logger': logging.getLogger(__name__), 'output_base_dir': output}
    exec(compile(ast.Module(body=[guard], type_ignores=[]), 'native_guard', 'exec'), scope)
    Path(output).mkdir(parents=True, exist_ok=True)
    (Path(output) / 'existing.json').write_text('{}')
    with pytest.raises(SystemExit):
        exec(compile(ast.Module(body=[guard], type_ignores=[]), 'native_guard', 'exec'), scope)


@pytest.mark.parametrize('benchmark_status', [0, 7])
@pytest.mark.parametrize('snapshot_status', [0, 9])
def test_post_measurement_snapshot_preserves_benchmark_status(tmp_path, benchmark_status, snapshot_status):
    source = RUNNER.read_text()
    block = source[source.index('# Do not populate the native output directory'):]
    tool = tmp_path / 'bin/python'
    tool.parent.mkdir()
    tool.write_text(f'#!/bin/sh\nexit {snapshot_status}\n')
    tool.chmod(0o755)
    native = tmp_path / 'run/native'
    native.mkdir(parents=True)
    (native / 'result.txt').write_text('measurement')
    script = '''set -euo pipefail
info() { :; }; error() { :; }; step() { :; }; warn() { :; }; docker() { :; }
COMMON_OUTPUT_DIR="$1/run"
VIA_OUTPUT_DIR="$COMMON_OUTPUT_DIR/native"
VENV_DIR="$1"
PERF_ROOT="$1"
image_tool=images.py
benchmark_exit="$2"
DOCKER_PREFIX=""
COMPOSE_FILE_ARGS=()
''' + block
    result = subprocess.run(['bash', '-c', script, 'runner', str(tmp_path), str(benchmark_status)],
                            text=True, capture_output=True)
    assert result.returncode == benchmark_status, result.stderr
    assert (native / 'result.txt').read_text() == 'measurement'

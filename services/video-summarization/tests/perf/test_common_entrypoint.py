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

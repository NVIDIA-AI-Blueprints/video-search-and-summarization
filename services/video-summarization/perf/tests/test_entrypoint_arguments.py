# SPDX-License-Identifier: Apache-2.0
"""Exercise argument validation without deployment, package installation or GPUs."""
from pathlib import Path
import subprocess

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/run_benchmark.sh'


def test_common_arguments_accept_metadata_and_preserve_diagnostics(tmp_path):
    output = tmp_path / 'run'
    diagnostics = output / 'diagnostics'
    diagnostics.mkdir(parents=True)
    marker = diagnostics / 'entrypoint.log'
    marker.write_text('existing diagnostics')
    result = subprocess.run(['bash', str(SCRIPT), '--compose-file', 'unused.yaml',
        '--config', 'auto', '--scenario', 'quick_test', '--output-json', str(output),
        '--config-id', 'config-1', '--triggered-by', 'manual', '--pipeline-url', 'https://ci/job/1/',
        '--vlm-gpus', '0', '--release', '3.3.0', '--help'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'CI/common interface' in result.stdout
    assert marker.read_text() == 'existing diagnostics'


def test_short_argument_help_remains_available():
    result = subprocess.run(['bash', str(SCRIPT), '-h'], capture_output=True, text=True)
    assert result.returncode == 0
    assert 'Usage:' in result.stdout


def test_existing_native_output_is_not_deleted(tmp_path):
    native = tmp_path / 'native'
    native.mkdir()
    marker = native / 'keep.txt'
    marker.write_text('previous native output')
    result = subprocess.run(['bash', str(SCRIPT), '--config', 'auto', '--output-json', str(tmp_path), '--help'],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert 'not empty' in result.stdout + result.stderr
    assert marker.read_text() == 'previous native output'


def test_common_invocation_requires_explicit_output_directory():
    result = subprocess.run(['bash', str(SCRIPT), '--config', 'auto', '--help'], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'requires --output-json' in result.stdout + result.stderr

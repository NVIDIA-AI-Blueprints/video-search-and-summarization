# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from copy import deepcopy
import logging
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

PERF = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PERF / "benchmark"))
import benchmark_config  # noqa: E402
from base import BenchmarkBase  # noqa: E402


def test_upstream_api_defaults_are_preserved_from_any_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    defaults = BenchmarkBase._load_defaults(None)
    assert defaults["generate_captions_params"] == {"temperature": 0.7, "max_tokens": 85}
    assert defaults["generate_video_embeddings_params"] == {"chunk_duration": 10}
    assert defaults["global"]["output_dir"] == "rtvi-embed-perf-report"


@pytest.mark.parametrize("text,match", [
    ("", "must be a mapping"),
    ("[", "Invalid YAML"),
    ("[]", "must be a mapping"),
    ("global: {}", "generate_captions_params"),
    ("generate_captions_params: {}", "generate_video_embeddings_params"),
    ("generate_captions_params: []", "generate_captions_params"),
])
def test_invalid_defaults_fail_with_the_source_path(tmp_path, text, match):
    path = tmp_path / "defaults.yaml"
    path.write_text(text)
    with pytest.raises(ValueError, match=match) as error:
        benchmark_config.load_defaults(path)
    assert str(path) in str(error.value)


def test_missing_defaults_fail_clearly(tmp_path):
    path = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError, match=str(path)):
        benchmark_config.load_defaults(path)


def test_native_config_loader_identifies_missing_defaults_not_scenario_file(tmp_path, monkeypatch):
    path = tmp_path / "missing-defaults.yaml"
    monkeypatch.setattr(benchmark_config, "DEFAULTS_PATH", path)
    with pytest.raises(FileNotFoundError, match=str(path)):
        BenchmarkBase.load_config_file(None, str(PERF / "configs/config.yaml"))


def test_execution_preserves_explicit_api_overrides():
    class ConfigHarness:
        _load_defaults = BenchmarkBase._load_defaults
        _merge_with_defaults = BenchmarkBase._merge_with_defaults
        logger = logging.getLogger("configuration-test")

    config = {"global": {
        "vlm_gpus": [1],
        "generate_captions_params": {"temperature": 0.2},
        "generate_video_embeddings_params": {"chunk_duration": 4},
    }}
    result = BenchmarkBase.parse_global_config(ConfigHarness(), config)
    assert result["generate_captions_params"] == {"temperature": 0.2, "max_tokens": 85}
    assert result["generate_video_embeddings_params"] == {"chunk_duration": 4}
    assert result["vlm_gpus"] == [1]


def test_preflight_reuses_execution_validation_without_mutating_configuration():
    config = yaml.safe_load((PERF / "configs/config.yaml").read_text())
    original = deepcopy(config)
    benchmark_config.validate_benchmark_config(config, "warmup_test")
    assert config == original
    with pytest.raises(ValueError, match="not found"):
        benchmark_config.validate_benchmark_config(config, "missing_scenario")


@pytest.mark.parametrize("change,match", [
    (lambda config: config.update({"global": []}), "global"),
    (lambda config: config.update(test_scenarios=[]), "test_scenarios"),
    (lambda config: config["global"].update(generate_captions_params=[]), "generate_captions_params"),
])
def test_preflight_rejects_invalid_structure(change, match):
    config = yaml.safe_load((PERF / "configs/config.yaml").read_text())
    change(config)
    with pytest.raises(ValueError, match=match):
        benchmark_config.validate_benchmark_config(config)


def test_generator_rejects_bad_selection_before_writing_runtime_config(tmp_path):
    output = tmp_path / "runtime-config.yaml"
    result = subprocess.run([
        sys.executable, str(PERF / "scripts/prepare_runtime_config.py"),
        "--source", str(PERF / "configs/config.yaml"),
        "--destination", str(output), "--native-output", str(tmp_path / "native"),
        "--scenario", "missing_scenario",
    ], cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode != 0
    assert "missing_scenario" in result.stderr
    assert not output.exists()


def test_runner_preflights_before_starting_native_setup():
    runner = (PERF / "scripts/run_benchmark.sh").read_text()
    assert runner.index('"$PERF_ROOT/scripts/prepare_runtime_config.py"') < runner.index(
        '\n"$PERF_ROOT/scripts/native_setup_perf_env.sh"'
    )

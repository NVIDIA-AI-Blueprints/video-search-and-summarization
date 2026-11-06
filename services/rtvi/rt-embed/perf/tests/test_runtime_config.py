# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from copy import deepcopy
from pathlib import Path
import os
import subprocess
import sys

import pytest
import yaml


PERF_ROOT = Path(__file__).resolve().parents[1]
PREPARE = PERF_ROOT / "scripts/prepare_runtime_config.py"
UPDATE = PERF_ROOT / "scripts/update_runtime_config.py"
SOURCE = PERF_ROOT / "configs/config.yaml"
RTSP_URL = "rtsp://127.0.0.1:30554/live/warehouse"
BACKEND_URL = "http://localhost:8011/v1"
DCGM_URL = "http://localhost:9400/metrics"
NODE_URL = "http://localhost:9101/metrics"
INITIAL_STREAM_COUNT = 7


def _prepared_config(tmp_path: Path) -> Path:
    destination = tmp_path / "runtime-config.yaml"
    subprocess.run(
        [
            sys.executable,
            str(PREPARE),
            "--source",
            str(SOURCE),
            "--destination",
            str(destination),
            "--native-output",
            str(tmp_path / "native"),
            "--gpus",
            "0,1",
        ],
        check=True,
    )
    return destination


def _update(path: Path, rtsp_url: str = RTSP_URL) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(UPDATE),
            "--config",
            str(path),
            "--rtsp-url",
            rtsp_url,
            "--rtvi-backend",
            BACKEND_URL,
            "--dcgm-exporter-url",
            DCGM_URL,
            "--node-exporter-url",
            NODE_URL,
            "--initial-stream-count",
            str(INITIAL_STREAM_COUNT),
        ],
        text=True,
        capture_output=True,
    )


def _apply_expected_runtime_values(config: dict) -> dict:
    expected = deepcopy(config)
    expected["global"]["rtvi_backend"] = BACKEND_URL
    prometheus = expected["global"]["gpu_monitoring"]["prometheus"]
    prometheus["dcgm_exporter_url"] = DCGM_URL
    prometheus["node_exporter_url"] = NODE_URL
    for scenario in expected["test_scenarios"].values():
        for video in scenario.get("videos") or []:
            if "rtsp_url" in video:
                video["rtsp_url"] = RTSP_URL
            if "initial_stream_count" in video:
                video["initial_stream_count"] = INITIAL_STREAM_COUNT
    return expected


def test_updates_runtime_values_without_changing_benchmark_semantics(tmp_path):
    source_before = SOURCE.read_bytes()
    runtime = _prepared_config(tmp_path)
    assert "rtsp_url: RTSP_STREAM_URL" in runtime.read_text()
    before = yaml.safe_load(runtime.read_text())

    result = _update(runtime)

    assert result.returncode == 0, result.stderr
    after = yaml.safe_load(runtime.read_text())
    assert after == _apply_expected_runtime_values(before)
    assert after["global"]["vlm_gpus"] == [0, 1]
    assert "RTSP_STREAM_URL" not in runtime.read_text()
    assert SOURCE.read_bytes() == source_before


def test_invalid_rtsp_url_leaves_runtime_config_unchanged(tmp_path):
    runtime = _prepared_config(tmp_path)
    before = runtime.read_bytes()

    result = _update(runtime, "http://localhost/not-rtsp")

    assert result.returncode != 0
    assert runtime.read_bytes() == before


def test_missing_rtsp_fields_leaves_runtime_config_unchanged(tmp_path):
    runtime = _prepared_config(tmp_path)
    config = yaml.safe_load(runtime.read_text())
    for scenario in config["test_scenarios"].values():
        for video in scenario.get("videos") or []:
            video.pop("rtsp_url", None)
    runtime.write_text(yaml.safe_dump(config, sort_keys=False))
    before = runtime.read_bytes()

    result = _update(runtime)

    assert result.returncode != 0
    assert runtime.read_bytes() == before


def test_malformed_yaml_leaves_runtime_config_unchanged(tmp_path):
    runtime = tmp_path / "runtime-config.yaml"
    runtime.write_text("global:\n  broken: [\n")
    before = runtime.read_bytes()

    result = _update(runtime)

    assert result.returncode != 0
    assert runtime.read_bytes() == before


def _setup_config_path(tmp_path, **values):
    # Execute the real setup path-selection block without deploying containers.
    setup = (PERF_ROOT / "scripts/native_setup_perf_env.sh").read_text()
    selection = setup.split('BENCHMARK_DIR="${SCRIPT_DIR}/benchmark"', 1)[1]
    selection = selection.split('REQUIREMENTS_FILE=', 1)[0]
    env = os.environ.copy()
    for name in ("BENCHMARK_CONFIG", "CONFIG"):
        env.pop(name, None)
    env.update(BENCHMARK_DIR=str(PERF_ROOT / "benchmark"), **values)
    result = subprocess.run(
        ["bash", "-c", 'set -euo pipefail\n' + selection
         + '\nprintf "%s" "$BENCHMARK_CONFIG"'],
        cwd=tmp_path, env=env, text=True, capture_output=True, check=True,
    )
    return Path(result.stdout)


def test_setup_updates_the_runner_config_even_with_legacy_config_set(tmp_path):
    runtime = _prepared_config(tmp_path)
    before = yaml.safe_load(runtime.read_text())
    source_before = SOURCE.read_bytes()
    selected = _setup_config_path(
        tmp_path, BENCHMARK_CONFIG=str(runtime), CONFIG=str(SOURCE)
    )
    assert selected == runtime
    result = _update(selected)
    assert result.returncode == 0, result.stderr
    assert yaml.safe_load(runtime.read_text()) == _apply_expected_runtime_values(before)
    assert "RTSP_STREAM_URL" not in runtime.read_text()
    assert SOURCE.read_bytes() == source_before


@pytest.mark.parametrize("config", [None, "custom.yaml", "configs/custom.yaml", "/tmp/custom.yaml"])
def test_standalone_setup_retains_legacy_config_resolution(tmp_path, config):
    values = {} if config is None else {"CONFIG": config}
    selected = _setup_config_path(tmp_path, **values)
    if config is None:
        expected = PERF_ROOT / "benchmark/rtvi_embed_bcd_config.yaml"
    elif config.startswith("/"):
        expected = Path(config)
    elif "/" in config:
        expected = tmp_path / config
    else:
        expected = PERF_ROOT / "benchmark" / config
    assert selected == expected

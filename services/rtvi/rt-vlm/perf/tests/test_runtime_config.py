# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from copy import deepcopy
from pathlib import Path
import subprocess
import sys

import yaml
import pytest


PERF_ROOT = Path(__file__).resolve().parents[1]
PREPARE = PERF_ROOT / "scripts/prepare_runtime_config.py"
UPDATE = PERF_ROOT / "scripts/update_runtime_config.py"
SOURCE = PERF_ROOT / "configs/config.yaml"
RTSP_URL = "rtsp://127.0.0.1:30554/live/warehouse"
BACKEND_URL = "http://localhost:8010/v1"
DCGM_URL = "http://localhost:9400/metrics"
NODE_URL = "http://localhost:9102/metrics"


@pytest.mark.parametrize(
    "config_id,profile",
    [
        ("rtvi-vlm-dgx-spark-1gpu", "benchmark/rtvi_vlm_bcd_3_3_dgx_spark_config.yaml"),
        ("rtvi-vlm-rtxpro6000bw-se-1gpu", "configs/config.yaml"),
        ("local-rtvi-vlm", "configs/config.yaml"),
    ],
)
@pytest.mark.parametrize("custom_source", [False, True])
def test_hardware_profile_selection_preserves_native_settings(
    tmp_path, config_id, profile, custom_source
):
    original = SOURCE.read_bytes()
    selected = PERF_ROOT / profile
    selected_before = selected.read_bytes()
    source = SOURCE
    if custom_source:
        source = tmp_path / "custom.yaml"
        source.write_bytes(original)
        selected = source
    expected = yaml.safe_load(selected.read_text())
    destination = tmp_path / "runtime.yaml"
    native_output = tmp_path / "native"
    subprocess.run(
        [sys.executable, str(PREPARE), "--source", str(source),
         "--destination", str(destination), "--native-output", str(native_output),
         "--config-id", config_id, "--gpus", "3", "--release", "test-release"],
        check=True,
    )
    expected["global"].update(
        output_dir=str(native_output.resolve()), vlm_gpus=[3], release="test-release"
    )
    assert yaml.safe_load(destination.read_text()) == expected
    assert SOURCE.read_bytes() == original
    assert (PERF_ROOT / profile).read_bytes() == selected_before


def test_spark_scenarios_are_available_to_the_common_queue():
    spark = yaml.safe_load(
        (PERF_ROOT / "benchmark/rtvi_vlm_bcd_3_3_dgx_spark_config.yaml").read_text()
    )
    standard = yaml.safe_load(SOURCE.read_text())
    assert set(spark["test_scenarios"]) == set(standard["test_scenarios"])
    assert spark["global"]["model_preset"] == "cr3-nano-reasoner-nvfp4"
    matrix = yaml.safe_load((PERF_ROOT / "configs/perf-configs.yaml").read_text())
    row = next(r for r in matrix["perf_configs"] if r["nodeLabel"] == "DGX-SPARK")
    assert row["enabled"] is True
    assert row["imageArch"] == "arm64-sbsa"


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
    return expected


def test_updates_unquoted_placeholder_without_changing_benchmark_semantics(tmp_path):
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

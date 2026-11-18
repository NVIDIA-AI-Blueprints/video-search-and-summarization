# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate RTVI hardware profiles without deploying services or changing workloads."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
ARCHITECTURES = {
    "RTXPRO6000BW-SE": "amd64", "H100-SXM": "amd64", "RTXPRO4500BW": "amd64",
    "DGX-SPARK": "arm64-sbsa", "IGX-THOR": "arm64-igpu", "GB300": "arm64-sbsa",
}
VLM_PROFILES = {
    "H100-SXM": ("h100_sxm", "cr3-super-reasoner-fp8"),
    "RTXPRO4500BW": ("rtx_pro_4500", "cr3-nano-reasoner-nvfp4"),
    "DGX-SPARK": ("dgx_spark", "cr3-nano-reasoner-nvfp4"),
    "IGX-THOR": ("agx_thor_t5000", "cr3-nano-reasoner-nvfp4"),
    "GB300": ("gb300", "cr3-super-reasoner-nvfp4"),
}


def load(path):
    return yaml.safe_load(path.read_text())


@pytest.mark.parametrize("service", ["rt-vlm", "rt-embed"])
def test_matrix_preserves_baseline_and_selects_native_profiles(service):
    root = ROOT / "services/rtvi" / service / "perf"
    rows = load(root / "configs/perf-configs.yaml")["perf_configs"]
    assert len(rows) == len({r["id"] for r in rows}) == 6
    assert {r["nodeLabel"]: r["imageArch"] for r in rows} == ARCHITECTURES
    baseline = rows[0]
    prefix = "rtvi-vlm" if service == "rt-vlm" else "rtvi-embed"
    assert baseline["id"] == f"{prefix}-rtxpro6000bw-se-1gpu"
    assert "benchmarkConfig" not in baseline  # Keep the existing default profile.
    assert baseline["vlmModel"] == ("cr3-super-reasoner-nvfp4" if service == "rt-vlm" else "cosmos-embed1-448p")
    assert baseline["llmGpus"] == baseline["llmModel"] == "unused"
    baseline_scenarios = set(load(root / "configs/config.yaml")["test_scenarios"])
    for row in rows:
        assert row["enabled"] is True
        assert row["vlmGpus"] == "0"
        assert (root.parent / row["composePath"]).is_file()
        assert row["checkoutPaths"] == baseline["checkoutPaths"]
        profile_path = root / row.get("benchmarkConfig", "configs/config.yaml")
        profile = load(profile_path)
        assert set(profile["test_scenarios"]) == baseline_scenarios
        if service == "rt-vlm" and row["nodeLabel"] != "RTXPRO6000BW-SE":
            platform, model = VLM_PROFILES[row["nodeLabel"]]
            assert profile_path.name == f"rtvi_vlm_bcd_3_3_{platform}_config.yaml"
            assert row["vlmModel"] == profile["global"]["model_preset"] == model
        elif service == "rt-embed":
            expected = ("benchmark/rtvi_embed_bcd_3_2_thor_config.yaml"
                        if row["nodeLabel"] in {"DGX-SPARK", "IGX-THOR"} else "configs/config.yaml")
            assert row.get("benchmarkConfig", "configs/config.yaml") == expected


@pytest.mark.parametrize("service", ["rt-vlm", "rt-embed"])
def test_preparation_preserves_every_profile_workload(service, tmp_path):
    root = ROOT / "services/rtvi" / service / "perf"
    rows = load(root / "configs/perf-configs.yaml")["perf_configs"]
    for row in rows:
        source = root / row.get("benchmarkConfig", "configs/config.yaml")
        before = source.read_bytes()
        expected = deepcopy(load(source))
        expected["global"].update(output_dir=str(tmp_path / "native"), release="3.4.0", vlm_gpus=[1])
        destination = tmp_path / "runtime.yaml"
        subprocess.run([
            sys.executable, str(root / "scripts/prepare_runtime_config.py"),
            "--source", str(source), "--destination", str(destination),
            "--native-output", str(tmp_path / "native"), "--release", "3.4.0", "--gpus", "1",
        ], check=True, capture_output=True, text=True)
        assert load(destination) == expected
        assert source.read_bytes() == before


@pytest.mark.parametrize("service", ["rt-vlm", "rt-embed"])
def test_report_metadata_preserves_measurements_and_outcomes(service, tmp_path):
    root = ROOT / "services/rtvi" / service / "perf"
    source = root / "configs/config.yaml"
    original = {
        "metadata": {"config": {"config_id": "test"}, "run_info": {"run_id": "original"}},
        "test_cases": [{"metrics": {"latency": 1.5}, "status": "FAIL"}],
        "summary": {"overall_status": "FAIL", "passed": 0, "failed": 1},
    }
    result = tmp_path / "result.json"
    result.write_text(json.dumps(original))
    subprocess.run([
        sys.executable, str(root / "scripts/finalize_result_metadata.py"),
        "--results", str(tmp_path), "--triggered-by", "manual", "--pipeline-url", "https://jenkins.test/1/",
        "--config", str(source), "--profile", str(source),
    ], check=True, env=dict(os.environ, GPU_MODEL="IGX-THOR"), capture_output=True, text=True)
    actual = json.loads(result.read_text())
    assert actual["test_cases"] == original["test_cases"]
    assert actual["summary"] == original["summary"]
    config = actual["metadata"]["config"]
    assert config["config_id"] == "test"
    assert config["benchmark_profile"] == "configs/config.yaml"
    assert config["gpu_count"] == 1
    assert config["gpu_model"] == "IGX-THOR"
    assert config["vlm"] == load(source)["global"].get("model_preset", "cosmos-embed1-448p")
    assert actual["metadata"]["run_info"]["run_id"] == "original"


@pytest.mark.parametrize("service_path", [
    "services/video-summarization", "services/analytics/behavior-analytics",
    "services/analytics/video-analytics-api", "services/alert",
    "services/rtvi/rt-vlm", "services/rtvi/rt-embed",
])
def test_all_service_thor_targets_use_igpu_classification(service_path):
    rows = load(ROOT / service_path / "perf/configs/perf-configs.yaml")["perf_configs"]
    thor = [row for row in rows if row.get("nodeLabel") in {"IGX-THOR", "IGX-T7000", "AGX-THOR"}]
    assert thor, f"Expected existing Thor configuration for {service_path}"
    assert all(row["imageArch"] == "arm64-igpu" for row in thor)

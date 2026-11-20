# SPDX-License-Identifier: Apache-2.0
"""Validate platform routing and Compose interpolation without deploying services."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

PERF = Path(__file__).resolve().parents[1]
COMPOSE = PERF / "setup/compose"
SCRIPT = PERF / "scripts/run_benchmark.sh"


def rows():
    return yaml.safe_load((PERF / "configs/perf-configs.yaml").read_text())["perf_configs"]


def test_all_six_p0_targets_enabled_without_removing_existing_platforms():
    matrix = rows()
    assert len({row["id"] for row in matrix}) == len(matrix)
    expected = {"H100-SXM": "amd64", "RTXPRO6000BW-SE": "amd64", "RTXPRO4500BW": "amd64",
                "DGX-SPARK": "arm64-sbsa", "IGX-THOR": "arm64-igpu", "GB300": "arm64-sbsa"}
    for platform, arch in expected.items():
        selected = [row for row in matrix if row["nodeLabel"] == platform]
        assert selected and all(row["enabled"] is True and row["imageArch"] == arch for row in selected)
    assert {"AGX-THOR", "GB200", "B200", "L40S"} <= {row["nodeLabel"] for row in matrix}
    for row in matrix:
        assert (COMPOSE / Path(row["composePath"]).name).is_file()


@pytest.mark.parametrize("platform,source", [("IGX-THOR", "AGX-THOR"), ("GB300", "DGX-SPARK")])
def test_new_platforms_reuse_approved_source_topology_and_workload(platform, source):
    matrix = rows()
    target = next(row for row in matrix if row["nodeLabel"] == platform)
    baseline = next(row for row in matrix if row["nodeLabel"] == source)
    excluded = {"id", "nodeLabel", "description", "enabled"}
    assert {k: v for k, v in target.items() if k not in excluded} == {
        k: v for k, v in baseline.items() if k not in excluded}


@pytest.mark.parametrize("name,overlay", [
    ("NVIDIA GB300", "gpu-gb300.yaml"), ("NVIDIA B300", "gpu-gb300.yaml"),
    ("NVIDIA GB200", "gpu-gb200.yaml"), ("NVIDIA B200", "gpu-b200.yaml"),
    ("NVIDIA Thor", "gpu-thor.yaml"), ("NVIDIA RTX PRO 4500 Blackwell", "gpu-rtx-pro-4500.yaml"),
])
def test_gpu_detection_preserves_existing_mappings(name, overlay):
    source = SCRIPT.read_text()
    function = source[source.index("match_device_overlay() {"):source.index("detect_gpu_overlay() {")]
    result = subprocess.run(["bash", "-c", function + '\nmatch_device_overlay "$1"', "test", name],
                            check=True, text=True, capture_output=True)
    assert result.stdout.strip() == overlay


@pytest.mark.parametrize("mode,clock_exit,success", [("MAXN", "0", True), ("OTHER", "0", False), ("MAXN", "1", False)])
def test_igx_keeps_mandatory_source_thor_power_setup(tmp_path, mode, clock_exit, success):
    source = SCRIPT.read_text()
    block = source[source.index("# --- Thor: MAXN"):source.index("STACK_LAUNCH_ATTEMPTED=false")]
    selected = next(row for row in rows() if row["nodeLabel"] == "IGX-THOR")
    calls = tmp_path / "calls"
    mocks = '''
step() { :; }
info() { :; }
error() { echo "$*" >&2; }
id() { echo 0; }
nvpmodel() { echo "nvpmodel $*" >> "$TEST_CALLS"; if [ "$1" = -q ]; then echo "NV Power Mode: $TEST_MODE"; fi; }
jetson_clocks() { echo jetson_clocks >> "$TEST_CALLS"; return "$TEST_CLOCK_EXIT"; }
'''
    result = subprocess.run(["bash", "-c", mocks + block], capture_output=True, text=True,
        env={**os.environ, "COMPOSE_FILE_PATH": selected["composePath"], "TEST_CALLS": str(calls),
             "TEST_MODE": mode, "TEST_CLOCK_EXIT": clock_exit})
    assert (result.returncode == 0) == success
    assert "nvpmodel -m 0" in calls.read_text()
    if mode == "MAXN":
        assert "jetson_clocks" in calls.read_text()


@pytest.mark.parametrize("platform,overlay", [
    ("RTXPRO4500BW", "gpu-rtx-pro-4500.yaml"), ("IGX-THOR", "gpu-thor.yaml"), ("GB300", "gpu-gb300.yaml"),
])
def test_new_targets_render_exact_image_and_gpu_assignment(platform, overlay):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required for offline rendering")
    row = next(row for row in rows() if row["nodeLabel"] == platform)
    images = yaml.safe_load((PERF / "setup/configs/images.yaml").read_text())["images"]
    target = "registry.example/lvs:owner-selected-tag"
    result = subprocess.run(["docker", "compose", "-f", str(COMPOSE / Path(row["composePath"]).name),
                             "-f", str(COMPOSE / "overlays" / overlay), "config", "--format", "json"],
                            env={"PATH": os.environ["PATH"], **images, "LVS_IMAGE": target, "COMPOSE_PROFILES": "media",
                                 "RTVI_GPU_0": row["vlmGpus"], "LLM_GPU_0": row["llmGpus"]},
                            text=True, capture_output=True, check=True)
    services = json.loads(result.stdout)["services"]
    assert services["lvs"]["image"] == target
    assert services["rtvi-vlm"]["image"] == images["PERF_RTVI_VLM_SBSA_IMAGE" if platform == "GB300" else "PERF_RTVI_VLM_IMAGE"]
    assert "cosmos3-nano-reasoner:modelopt-nvfp4" in services["rtvi-vlm"]["environment"]["MODEL_PATH"]
    llm_service = "nemotron-nano-9b-v2" if platform == "RTXPRO4500BW" else "nemotron-nano-9b-v2-dgx-spark"
    for service in ["rtvi-vlm", llm_service]:
        assert services[service]["deploy"]["resources"]["reservations"]["devices"][0]["device_ids"] == ["0"]

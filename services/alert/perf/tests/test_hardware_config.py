# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import sys

import pytest
import yaml

PERF = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PERF / "benchmark/core"))
from hardware_config import profile_for_config, remote_llm_arguments, supporting_images_for_profile  # noqa: E402


def rows():
    return yaml.safe_load((PERF / "configs/perf-configs.yaml").read_text())["perf_configs"]


def test_matrix_enables_all_p0_platforms_with_correct_architecture():
    matrix = rows()
    assert len({row["id"] for row in matrix}) == len(matrix) == 6
    assert {row["nodeLabel"] for row in matrix} == {
        "RTXPRO6000BW-SE", "H100-SXM", "RTXPRO4500BW",
        "DGX-SPARK", "IGX-THOR", "GB300",
    }
    assert all(row["enabled"] is True for row in matrix)
    for row in matrix:
        arm = row["nodeLabel"] in {"DGX-SPARK", "IGX-THOR", "GB300"}
        assert row["imageArch"] == ("arm64-igpu" if arm else "amd64")
        assert "deploy/docker/" in row["checkoutPaths"]
        if row["nodeLabel"] in {"DGX-SPARK", "IGX-THOR"}:
            assert not row.get("vlmGpus") and not row.get("llmGpus")
        if row["nodeLabel"] == "GB300":
            assert row["vlmGpus"] == row["llmGpus"] == "0"


@pytest.mark.parametrize("suffix,detected,expected", [
    ("h100", "H100", "H100"),
    ("rtxpro4500bw", "RTXPRO4500BW", "RTXPRO4500BW"),
    ("dgx-spark", "DGX-SPARK", "DGX-SPARK"),
    ("igx-thor", "AGX-THOR", "IGX-THOR"),
    ("gb300", "GB300", "GB300"),
])
def test_native_profile_mapping(suffix, detected, expected):
    assert profile_for_config("alerts-verification-compose-" + suffix, detected) == expected


def test_wrong_worker_rejected_instead_of_changing_hardware_profile():
    with pytest.raises(ValueError, match="requires IGX-THOR, detected H100"):
        profile_for_config("alerts-verification-compose-igx-thor", "H100")


@pytest.mark.parametrize("config_id", ["alerts-verification-compose", "custom-local-run"])
def test_original_local_detection_remains_compatible(config_id):
    assert profile_for_config(config_id, "H100") == "H100"


def test_hardware_matrix_does_not_override_workload_or_image_tags():
    for row in rows():
        assert not ({"workload", "benchmarkConfig", "supporting_images", "artifact_ref"} & row.keys())


@pytest.mark.parametrize("profile", ["RTXPRO4500BW", "DGX-SPARK"])
def test_remote_llm_uses_explicit_orchestrator_endpoint(profile):
    args = remote_llm_arguments(profile, {"ALERTS_PERF_REMOTE_LLM_URL": "http://llm.example:32088"})
    assert args == ["--use-remote-llm", "--llm-url", "http://llm.example:32088"]
    assert "--use-remote-vlm" not in args


@pytest.mark.parametrize("endpoint", ["", "file:///tmp/model", "http://u:p@host", "http://host:bad", "http://host:70000", "http://host/?key=secret", "http://host/#part", "http://bad host"])
def test_remote_endpoint_must_be_present_and_credential_free(endpoint):
    with pytest.raises(ValueError, match="requires ALERTS_PERF_REMOTE_LLM_URL"):
        remote_llm_arguments("DGX-SPARK", {"ALERTS_PERF_REMOTE_LLM_URL": endpoint})


@pytest.mark.parametrize("profile", ["H100", "RTXPRO6000BW", "IGX-THOR", "GB300"])
def test_other_hardware_ignores_shared_remote_llm_setting(profile):
    assert remote_llm_arguments(profile, {"ALERTS_PERF_REMOTE_LLM_URL": "http://shared"}) == []


def test_gb300_uses_configured_sbsa_support_images_without_changing_input():
    images = yaml.safe_load((PERF / "setup/configs/images.yaml").read_text())["images"]
    before = dict(images)
    selected = supporting_images_for_profile("GB300", images)
    for component in ("RT_VLM", "RT_CV"):
        key = f"PERF_VSS_{component}_IMAGE"
        assert selected[key] == images[f"PERF_VSS_{component}_SBSA_IMAGE"]
        assert selected[key].endswith(":develop-latest-sbsa")
    assert images == before
    assert {key for key in images if selected[key] != images[key]} == {
        "PERF_VSS_RT_VLM_IMAGE", "PERF_VSS_RT_CV_IMAGE"}
    assert "PERF_ALERT_IMAGE" not in selected


@pytest.mark.parametrize("profile", ["IGX-THOR", "AGX-THOR", "DGX-SPARK", "H100", "RTXPRO6000BW", "RTXPRO4500BW"])
def test_other_platforms_keep_their_support_images(profile):
    images = yaml.safe_load((PERF / "setup/configs/images.yaml").read_text())["images"]
    assert supporting_images_for_profile(profile, images) == images


def test_gb300_support_images_remain_configurable():
    images = yaml.safe_load((PERF / "setup/configs/images.yaml").read_text())["images"]
    images["PERF_VSS_RT_VLM_SBSA_IMAGE"] = "registry.example/vlm:approved-sbsa"
    assert supporting_images_for_profile("GB300", images)["PERF_VSS_RT_VLM_IMAGE"] == "registry.example/vlm:approved-sbsa"

######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

START_SCRIPT = Path(__file__).parents[1] / "start_rtvi_vlm.sh"
REPO_ROOT = START_SCRIPT.parent
RUNTIME_VALIDATOR_PATH = "rtvi/utils/env_validation.py"
SOURCE_VALIDATOR_PATH = "src/utils/env_validation.py"


def _create_runtime_layout(root: Path) -> None:
    validator = root / RUNTIME_VALIDATOR_PATH
    validator.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "src/utils/env_validation.py", validator)


def _run_entrypoint_defaults(
    attention_backend: str | None = None,
    model_path: str = "ngc:nim/nvidia/cosmos3-super-reasoner:modelopt-nvfp4-test",
    gpu_name: str = "NVIDIA GB300",
    cudagraph_mode: str | None = None,
    gemm_backend: str | None = None,
    video_pruning_rate: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    prefix = START_SCRIPT.read_text(encoding="utf-8").split(
        "mkdir -p /tmp/rtvi-logs/", 1
    )[0]
    stubs = r"""
nvdec_get_count() { echo 8; }
python3() {
    if [ "$1" = "rtvi/utils/env_validation.py" ] || [ "$1" = "src/utils/env_validation.py" ]; then
        command python3 "__VALIDATOR_PATH__"
    else
        return 0
    fi
}
nvidia-smi() {
    case "$*" in
        *memory.free*) echo "250000 MiB" ;;
        *memory.total*) echo "250000 MiB" ;;
        *compute_cap*) echo "10.3" ;;
        *name*) echo "__GPU_NAME__" ;;
    esac
}
""".replace("__GPU_NAME__", gpu_name).replace(
        "__VALIDATOR_PATH__", str(REPO_ROOT / "src/utils/env_validation.py")
    )
    env = os.environ.copy()
    env.update(
        {
            "ASSET_STORAGE_DIR": "/does-not-exist",
            "MODEL_PATH": model_path,
            "NUM_GPUS": "1",
        }
    )
    if attention_backend is None:
        env.pop("VLLM_ATTENTION_BACKEND", None)
    else:
        env["VLLM_ATTENTION_BACKEND"] = attention_backend
    for name, value in (
        ("VLLM_CUDAGRAPH_MODE", cudagraph_mode),
        ("VLLM_NVFP4_GEMM_BACKEND", gemm_backend),
    ):
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    if video_pruning_rate is None:
        env.pop("VLM_VIDEO_PRUNING_RATE", None)
    else:
        env["VLM_VIDEO_PRUNING_RATE"] = video_pruning_rate
    probe = (
        '\nprintf "%s:%s|%s:%s|%s" '
        '"${VLLM_CUDAGRAPH_MODE+x}" "${VLLM_CUDAGRAPH_MODE-}" '
        '"${VLLM_NVFP4_GEMM_BACKEND+x}" "${VLLM_NVFP4_GEMM_BACKEND-}" '
        '"$VLLM_ATTENTION_BACKEND"\n'
    )
    return subprocess.run(
        [
            "bash",
            "-c",
            stubs + prefix + probe,
        ],
        check=check,
        capture_output=True,
        cwd=START_SCRIPT.parent,
        env=env,
        text=True,
    )


def test_cr3_super_nvfp4_gb300_defaults_to_triton_attention() -> None:
    output = _run_entrypoint_defaults().stdout

    assert output.endswith("TRITON_ATTN")
    assert "Defaulting attention backend to TRITON_ATTN" in output


def test_cr3_nano_gb300_defaults_to_triton_attention() -> None:
    output = _run_entrypoint_defaults(
        model_path="ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-test"
    ).stdout

    assert output.endswith("TRITON_ATTN")
    assert "Defaulting attention backend to TRITON_ATTN" in output


def test_cr3_super_fp8_gb300_defaults_to_triton_attention() -> None:
    output = _run_entrypoint_defaults(
        model_path="ngc:nim/nvidia/cosmos3-super-reasoner:modelopt-fp8-test"
    ).stdout

    assert output.endswith("TRITON_ATTN")
    assert "Defaulting attention backend to TRITON_ATTN" in output


def test_cr3_nano_non_gb300_does_not_default_to_triton_attention() -> None:
    output = _run_entrypoint_defaults(
        model_path="ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-test",
        gpu_name="NVIDIA H100 80GB HBM3",
    ).stdout

    assert not output.endswith("TRITON_ATTN")
    assert "Defaulting attention backend" not in output


def test_explicit_attention_backend_is_preserved() -> None:
    output = _run_entrypoint_defaults(
        "FLASHINFER",
        model_path="ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-test",
    ).stdout

    assert output.endswith("FLASHINFER")
    assert "Defaulting attention backend" not in output


def test_empty_graph_and_gemm_overrides_are_unset() -> None:
    output = _run_entrypoint_defaults(cudagraph_mode="", gemm_backend="").stdout

    assert output.endswith(":|:|TRITON_ATTN")


def test_explicit_graph_and_gemm_overrides_are_preserved() -> None:
    output = _run_entrypoint_defaults(
        cudagraph_mode="NONE", gemm_backend="cutlass"
    ).stdout

    assert output.endswith("x:NONE|x:cutlass|TRITON_ATTN")


def test_non_gb300_empty_cudagraph_behavior_is_unchanged() -> None:
    output = _run_entrypoint_defaults(
        gpu_name="NVIDIA H100 80GB HBM3", cudagraph_mode="", gemm_backend=""
    ).stdout

    assert output.endswith("x:|:|")


@pytest.mark.parametrize(
    "value", ["-0.5", "0", "1", "1.5", "nan", "inf", "not-a-number"]
)
def test_invalid_video_pruning_rate_stops_entrypoint(value: str) -> None:
    result = _run_entrypoint_defaults(video_pruning_rate=value, check=False)

    assert result.returncode != 0
    assert "VLM_VIDEO_PRUNING_RATE" in result.stderr
    assert "greater than 0 and less than 1" in result.stderr


@pytest.mark.parametrize("value", [None, "", "0.5", "0.999"])
def test_valid_or_unset_video_pruning_rate_allows_entrypoint(value: str | None) -> None:
    result = _run_entrypoint_defaults(video_pruning_rate=value)

    assert result.returncode == 0


@pytest.mark.parametrize("value", ["-0.5", "1.5"])
def test_full_entrypoint_rejects_reported_invalid_video_pruning_rates(
    value: str,
) -> None:
    env = os.environ.copy()
    env["VLM_VIDEO_PRUNING_RATE"] = value

    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_root = Path(temp_dir)
        _create_runtime_layout(runtime_root)
        runtime_entrypoint = runtime_root / START_SCRIPT.name
        shutil.copy2(START_SCRIPT, runtime_entrypoint)
        result = subprocess.run(
            ["bash", str(runtime_entrypoint)],
            check=False,
            capture_output=True,
            cwd=runtime_root,
            env=env,
            text=True,
        )

    assert result.returncode == 2
    assert "VLM_VIDEO_PRUNING_RATE" in result.stderr
    assert "greater than 0 and less than 1" in result.stderr
    assert "nvidia-smi" not in result.stderr


def test_environment_validator_is_packaged_for_runtime_and_public_release() -> None:
    runtime_files = (REPO_ROOT / "docker/rtvi_vlm/package_file_list.txt").read_text(
        encoding="utf-8"
    )
    release_files = (REPO_ROOT / "scripts/rt_vlm_release_file_list.txt").read_text(
        encoding="utf-8"
    )

    assert "utils/env_validation.py" in runtime_files.splitlines()
    assert "src/utils/env_validation.py" in release_files.splitlines()
    entrypoint = START_SCRIPT.read_text(encoding="utf-8")
    assert f"python3 {RUNTIME_VALIDATOR_PATH}" in entrypoint
    assert f"python3 {SOURCE_VALIDATOR_PATH}" in entrypoint


def test_ipc_environment_is_forwarded_to_server() -> None:
    script = START_SCRIPT.read_text(encoding="utf-8")

    assert "RTVI_IPC_FRAME_COPY:-false" in script
    assert "local -a ipc_args=()" in script
    assert '"${ipc_args[@]}"' in script
    assert 'local ipc_socket_dir="${RTVI_IPC_SOCKET_DIR:-/run/rtvi-ipc}"' in script
    assert "ipc_socket_template='nvds_ipc_{camera_id}.sock'" in script

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
import subprocess
from pathlib import Path

START_SCRIPT = Path(__file__).parents[1] / "start_rtvi_vlm.sh"


def _run_entrypoint_defaults(
    attention_backend: str | None = None,
    model_path: str = "ngc:nim/nvidia/cosmos3-super-reasoner:modelopt-nvfp4-test",
    gpu_name: str = "NVIDIA GB300",
) -> str:
    prefix = START_SCRIPT.read_text(encoding="utf-8").split("mkdir -p /tmp/rtvi-logs/", 1)[0]
    stubs = r"""
nvdec_get_count() { echo 8; }
python3() { return 0; }
nvidia-smi() {
    case "$*" in
        *memory.free*) echo "250000 MiB" ;;
        *memory.total*) echo "250000 MiB" ;;
        *compute_cap*) echo "10.3" ;;
        *name*) echo "__GPU_NAME__" ;;
    esac
}
""".replace("__GPU_NAME__", gpu_name)
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
    result = subprocess.run(
        ["bash", "-c", stubs + prefix + '\nprintf "%s" "$VLLM_ATTENTION_BACKEND"\n'],
        check=True,
        capture_output=True,
        cwd=START_SCRIPT.parent,
        env=env,
        text=True,
    )
    return result.stdout


def test_cr3_super_nvfp4_gb300_defaults_to_triton_attention() -> None:
    output = _run_entrypoint_defaults()

    assert output.endswith("TRITON_ATTN")
    assert "Defaulting attention backend to TRITON_ATTN" in output


def test_cr3_nano_gb300_defaults_to_triton_attention() -> None:
    output = _run_entrypoint_defaults(
        model_path="ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-test"
    )

    assert output.endswith("TRITON_ATTN")
    assert "Defaulting attention backend to TRITON_ATTN" in output


def test_cr3_super_fp8_gb300_defaults_to_triton_attention() -> None:
    output = _run_entrypoint_defaults(
        model_path="ngc:nim/nvidia/cosmos3-super-reasoner:modelopt-fp8-test"
    )

    assert output.endswith("TRITON_ATTN")
    assert "Defaulting attention backend to TRITON_ATTN" in output


def test_cr3_nano_non_gb300_does_not_default_to_triton_attention() -> None:
    output = _run_entrypoint_defaults(
        model_path="ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-test",
        gpu_name="NVIDIA H100 80GB HBM3",
    )

    assert not output.endswith("TRITON_ATTN")
    assert "Defaulting attention backend" not in output


def test_explicit_attention_backend_is_preserved() -> None:
    output = _run_entrypoint_defaults(
        "FLASHINFER", model_path="ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-test"
    )

    assert output.endswith("FLASHINFER")
    assert "Defaulting attention backend" not in output

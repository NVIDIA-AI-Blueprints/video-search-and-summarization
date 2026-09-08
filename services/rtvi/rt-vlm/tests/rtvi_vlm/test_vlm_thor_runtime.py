# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.  # noqa: E501
# SPDX-License-Identifier: Apache-2.0
"""Thor runtime packaging regression tests."""

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.mark.test_in_ci
def test_vlm_image_copies_and_loads_arm_nvjpeg_from_sbsa_target():
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text()
    sbsa_nvjpeg = "/tmp/triton-cuda-targets/sbsa-linux/lib/libnvjpeg.so*"
    arm_nvjpeg = "/tmp/triton-cuda-targets/aarch64-linux/lib/libnvjpeg.so*"
    sbsa_dir = "/usr/local/cuda/targets/sbsa-linux/lib"
    sbsa_ld_path = f"{sbsa_dir}:${{LD_LIBRARY_PATH}}"

    assert sbsa_nvjpeg in dockerfile
    assert sbsa_ld_path in dockerfile
    assert arm_nvjpeg not in dockerfile

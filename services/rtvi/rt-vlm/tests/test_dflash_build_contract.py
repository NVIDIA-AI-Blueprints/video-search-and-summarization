# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.test_in_ci


def test_dflash_runtime_is_explicit_and_default_build_is_unchanged():
    dockerfile = (ROOT / "docker/Dockerfile").read_text()
    stages = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert "FROM runtime AS dflash-runtime" in stages
    assert stages[-1] == "FROM runtime AS default"
    assert (
        "nvcr.io/nim/nvidia/cosmos3@sha256:dad096ce3552363e7ebf2f1805f8b0da099679af396a6581e6b2f2dfea7a9950"
        in dockerfile
    )
    experimental = dockerfile.split("FROM runtime AS dflash-runtime", 1)[1]
    assert 'test "$TARGETARCH" = "amd64"' in experimental
    assert "DFlashDraftModel" in experimental
    assert "USER vss:1001" in experimental


def test_both_compose_modes_forward_the_same_opt_in_setting():
    expected = "${RTVI_VLLM_SPECULATIVE_CONFIG-${VLLM_SPECULATIVE_CONFIG:-}}"
    for filename in ("compose.yaml", "compose.perf.yaml"):
        compose = yaml.safe_load((ROOT / "docker" / filename).read_text())
        assert (
            compose["services"]["rtvi-server"]["environment"]["RTVI_VLLM_SPECULATIVE_CONFIG"]
            == expected
        )

# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""OpenShell docker reset keeps model caches and pre-warms images."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openshell.docker_prep import (
    DOCKER_PREWARM_SCRIPT,
    DOCKER_RESET_SCRIPT,
    MODEL_CACHE_VOLUME_RE,
)

GOLDEN = (
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "docker"
    / "test-scripts"
    / "compose-images.golden"
)
CACHE_RE = re.compile(MODEL_CACHE_VOLUME_RE)


def test_reset_does_not_drop_every_volume() -> None:
    assert "drop=$(printf '%s\\n' $vols | grep -vE" in DOCKER_RESET_SCRIPT
    assert "CACHE_RE=" in DOCKER_RESET_SCRIPT
    assert "SKILL_EVAL_COLD_DOCKER_RESET" in DOCKER_RESET_SCRIPT
    assert "cache volumes kept" in DOCKER_RESET_SCRIPT


def test_cache_volume_names_are_kept() -> None:
    keep = (
        "vss_rtvi-hf-cache",
        "vss_rtvi-ngc-model-cache",
        "vss_cosmos3_reasoner_cache",
        "vss_vios_apt_cache",
        "nvidia_nemotron_nano_9b_v2_fp8_cache",
    )
    drop = (
        "vss_elastic-data",
        "vss_kafka-data",
        "vss_phoenix-data",
        "vss_vios_pg_data",
        "vss_logstash-libs",
    )
    for name in keep:
        assert CACHE_RE.search(name), name
    for name in drop:
        assert not CACHE_RE.search(name), name


def test_prewarm_skips_nims_and_industry_profiles() -> None:
    assert "nvcr.io/nim/" in DOCKER_PREWARM_SCRIPT
    assert "industry-profiles" in DOCKER_PREWARM_SCRIPT
    assert "xargs" in DOCKER_PREWARM_SCRIPT
    assert "compose-images.golden" in DOCKER_PREWARM_SCRIPT


def test_golden_list_has_developer_images_to_prewarm() -> None:
    assert GOLDEN.is_file()
    pullable = []
    for line in GOLDEN.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        src, image = parts[0], parts[1]
        if "industry-profiles/" in src:
            continue
        if "/" not in image or "${" in image or image.startswith("nvcr.io/nim/"):
            continue
        pullable.append(image)
    assert "ghcr.io/nvidia-ai-blueprints/vss/vss-rt-vlm:develop-latest" in pullable
    assert "ghcr.io/nvidia-ai-blueprints/vss/vss-agent:develop-latest" in pullable
    assert all(not item.startswith("nvcr.io/nim/") for item in pullable)

# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deployment-contract checks for the build-vision-agent skill."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[7]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "vss-build-vision-agent"


def test_resolved_deployment_refreshes_registry_images() -> None:
    deployment = (SKILL_ROOT / "references" / "deployment.md").read_text(encoding="utf-8")

    assert 'docker compose -f "$BUILD_DIR/resolved.yml" up -d --pull always' in deployment
    assert "moving tag" in deployment

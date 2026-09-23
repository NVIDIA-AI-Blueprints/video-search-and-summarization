#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check that public RT-VLM Compose variables appear in the config reference."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "services/rtvi/rt-vlm/docker/compose.yaml"
DOCS = ROOT / "docs/real-time-vlm.mdx"
README = ROOT / "services/rtvi/rt-vlm/README.md"
ENV_EXAMPLE = ROOT / "services/rtvi/rt-vlm/docker/.env.example"
LVS_COMPOSE = ROOT / "services/video-summarization/docker/deploy/compose.yaml"
LVS_README = ROOT / "services/video-summarization/README.md"
HELM_VALUES = ROOT / "deploy/helm/services/rtvi/charts/rtvi-vlm/values.yaml"
PUBLIC_IMAGE = "ghcr.io/nvidia-ai-blueprints/vss/vss-rt-vlm:develop-latest"


def main() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    service = compose.split("  rtvi-server:\n", 1)[1].split("\n  kafka:\n", 1)[0]
    compose_variables = set(re.findall(r"\$\{([A-Z][A-Z0-9_]+)", service))

    docs = DOCS.read_text(encoding="utf-8")
    table = docs.split("### Docker Compose and Helm Variables", 1)[1].split(
        "### Additional Helm Chart Values", 1
    )[0]
    documented_variables = set(re.findall(r"`([A-Z][A-Z0-9_]+)`", table))

    missing = sorted(compose_variables - documented_variables)
    assert not missing, f"Compose variables missing from {DOCS}: {', '.join(missing)}"
    assert "real-time-vlm.mdx#docker-compose-and-helm-variables" in README.read_text(
        encoding="utf-8"
    )

    public_files = (
        DOCS,
        README,
        ENV_EXAMPLE,
        COMPOSE,
        LVS_COMPOSE,
        LVS_README,
        HELM_VALUES,
    )
    for path in public_files:
        assert "nvcr.io/nvstaging/vss-core/vss-rt-vlm" not in path.read_text(
            encoding="utf-8"
        ), f"Private RT-VLM image found in {path}"

    for path in (ENV_EXAMPLE, COMPOSE, LVS_COMPOSE):
        assert PUBLIC_IMAGE in path.read_text(encoding="utf-8")

    helm_values = HELM_VALUES.read_text(encoding="utf-8")
    assert "repository: ghcr.io/nvidia-ai-blueprints/vss/vss-rt-vlm" in helm_values
    assert 'tag: "develop-latest"' in helm_values

    assert "README.md#container-image-availability" in docs
    assert "README.md#container-image-availability" in README.read_text(
        encoding="utf-8"
    )


if __name__ == "__main__":
    main()

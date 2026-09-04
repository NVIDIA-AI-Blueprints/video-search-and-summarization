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


def main() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    service = compose.split("  rtvi-server:\n", 1)[1].split("\n  kafka:\n", 1)[0]
    environment = service.split("    environment:\n", 1)[1].split("\n\n    ulimits:", 1)[0]
    compose_variables = set(re.findall(r"\$\{([A-Z][A-Z0-9_]+)", environment))

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


if __name__ == "__main__":
    main()

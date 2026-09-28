#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INDUSTRY_AGENT_CONFIGS = (
    ROOT
    / "deploy/docker/industry-profiles/warehouse-operations/vss-agent/configs/config.yml",
    ROOT / "deploy/docker/industry-profiles/smartcities/vss-agent/configs/config.yml",
)

# The chart's top-level `version:` is the one declared release line: Helm reads
# it, and build-dev-images.yml bakes it into every image as the version
# GET /api/v1/version reports.
AGENT_CHART = ROOT / "deploy/helm/services/agent/Chart.yaml"
# vss_core.version.SEMVER_PATTERN, the contract the endpoint enforces. Spelled
# out because these script tests run on a bare python3 with no VSS installed.
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def chart_version() -> str:
    for line in AGENT_CHART.read_text().splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    raise AssertionError(f"{AGENT_CHART} declares no top-level 'version:'")


class AgentConfigDefaultsTest(unittest.TestCase):
    def test_agent_version_has_unset_environment_fallback(self) -> None:
        for path in INDUSTRY_AGENT_CONFIGS:
            with self.subTest(path=path):
                lines = [line.strip() for line in path.read_text().splitlines()]
                self.assertIn("agent_version: ${VSS_AGENT_VERSION:-dev}", lines)
                self.assertNotIn("agent_version: ${VSS_AGENT_VERSION}", lines)


class ChartVersionTest(unittest.TestCase):
    def test_chart_version_is_strict_semver(self) -> None:
        """Whatever the chart says is baked into every image and served, so it
        has to satisfy the contract."""
        self.assertRegex(chart_version(), SEMVER)


if __name__ == "__main__":
    unittest.main()

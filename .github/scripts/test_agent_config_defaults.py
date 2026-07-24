#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INDUSTRY_AGENT_CONFIGS = (
    ROOT
    / "deploy/docker/industry-profiles/warehouse-operations/vss-agent/configs/config.yml",
    ROOT / "deploy/docker/industry-profiles/smartcities/vss-agent/configs/config.yml",
)


class AgentConfigDefaultsTest(unittest.TestCase):
    def test_agent_version_has_unset_environment_fallback(self) -> None:
        for path in INDUSTRY_AGENT_CONFIGS:
            with self.subTest(path=path):
                lines = [line.strip() for line in path.read_text().splitlines()]
                self.assertIn("agent_version: ${VSS_AGENT_VERSION:-dev}", lines)
                self.assertNotIn("agent_version: ${VSS_AGENT_VERSION}", lines)


if __name__ == "__main__":
    unittest.main()

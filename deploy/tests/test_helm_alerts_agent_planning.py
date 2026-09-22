# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Helm Alerts enables the planning path that carries incident lookup rules."""

from __future__ import annotations

from pathlib import Path
import unittest

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_CONFIG = REPO_ROOT / "helm/developer-profiles/dev-profile-alerts/configs/vss-agent/config.yml"


class AlertsAgentPlanningTests(unittest.TestCase):
    def test_exact_incident_lookup_rules_reach_enabled_planning_path(self) -> None:
        workflow = yaml.safe_load(AGENT_CONFIG.read_text())["workflow"]

        self.assertIs(workflow["planning_enabled"], True)
        plan_prompt = workflow["plan_prompt"]
        required_rules = (
            "pass it as `incident_id` for an exact lookup",
            "pass `vlm_verified=true` when the current request says verified",
            "`vlm_verified=false` when it says unverified",
            "omit `vlm_verified`",
            "If the exact lookup returns no incident",
        )
        for rule in required_rules:
            with self.subTest(rule=rule):
                self.assertIn(rule, plan_prompt)


if __name__ == "__main__":
    unittest.main()

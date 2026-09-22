# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the OpenClaw image's build-time config patch."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
MODULE_PATH = REPO_ROOT / ".openclaw" / "apply-onboard-config.py"
SPEC = importlib.util.spec_from_file_location("openclaw_onboard_config", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
onboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(onboard)


class TestOpenClawOnboardConfig(unittest.TestCase):
    def test_adds_idempotent_stateless_ui_agent_without_changing_main(self) -> None:
        main_agent = {
            "id": "main",
            "default": True,
            "workspace": "/sandbox/.openclaw/workspace",
        }
        config = {
            "agents": {"defaults": {}, "list": [main_agent.copy()]},
            "gateway": {"port": 18789},
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "openclaw.json"
            path.write_text(json.dumps(config), encoding="utf-8")

            changes = onboard.apply(str(path), {})
            first = json.loads(path.read_text(encoding="utf-8"))
            second_changes = onboard.apply(str(path), {})
            second = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(any("vss-ui" in change for change in changes))
        self.assertEqual(second_changes, [])
        self.assertEqual(first, second)
        self.assertEqual(first["agents"]["list"][0], main_agent)

        ui_agents = [
            agent for agent in first["agents"]["list"] if agent.get("id") == "vss-ui"
        ]
        self.assertEqual(len(ui_agents), 1)
        ui_agent = ui_agents[0]
        self.assertEqual(ui_agent["workspace"], "/sandbox/.openclaw/workspace-vss-ui")
        self.assertEqual(ui_agent["memorySearch"], {"enabled": False})
        self.assertEqual(ui_agent["tools"]["allow"], ["read", "vss_cli"])
        self.assertEqual(ui_agent["tools"]["fs"], {"workspaceOnly": True})
        self.assertGreaterEqual(
            set(ui_agent["tools"]["deny"]),
            {
                "group:automation",
                "group:memory",
                "group:runtime",
                "group:sessions",
                "apply_patch",
                "edit",
                "write",
            },
        )

    def test_ui_workspace_has_no_durable_user_memory_files(self) -> None:
        workspace = REPO_ROOT / ".openclaw" / "workspace" / "_vss-ui"
        self.assertEqual(
            {path.name for path in workspace.iterdir()},
            {"AGENTS.md", "ENV.md", "IDENTITY.md", "SOUL.md"},
        )
        instructions = (workspace / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("A New chat starts", instructions)
        self.assertIn("Never read, create, or update `USER.md`", instructions)


if __name__ == "__main__":
    unittest.main()

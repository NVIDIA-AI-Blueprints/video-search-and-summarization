#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from advance_ghcr_alias import advance, alias_plan  # noqa: E402

DIGEST = "sha256:" + "1" * 64


def release_set(ref: str = "develop") -> dict:
    return {
        "source": {"ref": ref},
        "images": [
            {
                "name": "vss-agent",
                "strategy": "build",
                "image": "ghcr.io/nvidia-ai-blueprints/vss/vss-agent",
                "tag": "develop-deadbeef1234",
                "digest": DIGEST,
            },
            {
                "name": "vss-configurator",
                "strategy": "reuse-pinned",
                "image": "nvcr.io/nvidia/vss-core/vss-configurator",
                "tag": "3.2.1",
                "digest": None,
            },
        ],
    }


class AdvanceAliasTest(unittest.TestCase):
    def test_plan_selects_only_built_ghcr_images(self):
        plan = alias_plan(release_set(), "develop-validated")
        self.assertEqual(len(plan), 1)
        self.assertEqual(
            plan[0].target,
            "ghcr.io/nvidia-ai-blueprints/vss/vss-agent:develop-validated",
        )
        self.assertTrue(plan[0].source.endswith("@" + DIGEST))

    def test_non_develop_release_set_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "only from develop"):
            alias_plan(release_set("pull-request/1190"), "develop-validated")

    def test_advance_verifies_alias_digest(self):
        update = alias_plan(release_set(), "develop-validated")[0]
        commands: list[list[str]] = []

        def runner(command: list[str]) -> str:
            commands.append(command)
            return json.dumps({"digest": DIGEST}) if "inspect" in command else ""

        advance(update, runner)
        self.assertEqual(commands[0][3], "create")
        self.assertEqual(commands[1][3], "inspect")


if __name__ == "__main__":
    unittest.main(verbosity=2)

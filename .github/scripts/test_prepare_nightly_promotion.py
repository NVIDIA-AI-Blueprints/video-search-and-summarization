#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare_nightly_promotion import (  # noqa: E402
    promotion_variables,
    select_build_run,
)


def release_set() -> dict:
    return {
        "release_set_id": "sha256:" + "1" * 64,
        "images": [
            {
                "name": "vss-agent",
                "strategy": "build",
                "image": "ghcr.io/nvidia-ai-blueprints/vss-agent",
                "tag": "develop-deadbeef1234",
                "digest": "sha256:" + "2" * 64,
            }
        ],
    }


class NightlyPromotionTest(unittest.TestCase):
    def test_selects_exact_green_develop_run(self):
        runs = [
            {
                "id": 1,
                "head_branch": "feature",
                "head_sha": "a" * 40,
                "conclusion": "success",
            },
            {
                "id": 2,
                "head_branch": "develop",
                "head_sha": "b" * 40,
                "conclusion": "success",
            },
        ]
        self.assertEqual(select_build_run(runs)["id"], 2)
        self.assertIsNone(select_build_run(runs, "c" * 40))

    def test_promotion_variables_preserve_tag_and_manifest(self):
        payload = release_set()
        tag, variables = promotion_variables(
            payload,
            requested_tag="develop-deadbeef1234",
            agent_ui_config="configs/vss-3.2.0/vss-core-agent.yml",
        )
        self.assertEqual(tag, "develop-deadbeef1234")
        self.assertEqual(variables["BUILD_TYPE"], "ghcr-promotion")
        self.assertEqual(
            json.loads(base64.b64decode(variables["VSS_RELEASE_SET_B64"])),
            payload,
        )

    def test_agent_config_is_required(self):
        with self.assertRaisesRegex(ValueError, "config path"):
            promotion_variables(release_set())


if __name__ == "__main__":
    unittest.main(verbosity=2)

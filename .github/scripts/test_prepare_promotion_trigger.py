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

from prepare_promotion_trigger import promotion_variables  # noqa: E402


class PromotionTriggerTest(unittest.TestCase):
    def test_converts_test_handoff_to_promotion_only_mode(self):
        release_set = {
            "images": [
                {"name": "vss-agent", "strategy": "build"},
                {"name": "vss-alert-ms", "strategy": "build"},
            ]
        }
        encoded = base64.b64encode(json.dumps(release_set).encode()).decode()
        variables = promotion_variables(
            {
                "BUILD_TYPE": "ghcr-nightly",
                "VSS_RELEASE_SET_B64": encoded,
                "VSS_RELEASE_SET_ID": "sha256:" + "1" * 64,
                "VSS_PROMOTION_TAG": "develop-deadbeef",
                "VSS_ACCEPTANCE_REGISTRY": "ghcr",
            },
            "12345",
            agent_ui_config="configs/vss-3.2.0/vss-core-agent.yml",
            alert_config="configs/vss-3.2.0/vss-core-vlm-verifier.yml",
        )
        self.assertEqual(variables["BUILD_TYPE"], "ghcr-promotion")
        self.assertEqual(variables["VSS_TEST_PIPELINE_ID"], "12345")
        self.assertEqual(
            variables["AGENT_UI_ARTIFACTS_PROMOTION_CONFIG_PATH"],
            "configs/vss-3.2.0/vss-core-agent.yml",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

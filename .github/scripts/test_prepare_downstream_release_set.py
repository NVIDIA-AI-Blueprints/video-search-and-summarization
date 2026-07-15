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

from prepare_downstream_release_set import downstream_variables  # noqa: E402


class DownstreamVariablesTest(unittest.TestCase):
    def test_encodes_exact_release_set_for_acceptance(self):
        release_set = {
            "schema_version": 1,
            "release_set_id": "sha256:" + "1" * 64,
            "source": {"commit": "a" * 40},
            "images": [{"name": "vss-agent"}],
        }
        variables = downstream_variables(release_set)
        self.assertEqual(variables["BUILD_TYPE"], "ghcr-acceptance")
        self.assertEqual(
            variables["VSS_RELEASE_SET_ID"], release_set["release_set_id"]
        )
        decoded = json.loads(base64.b64decode(variables["VSS_RELEASE_SET_B64"]))
        self.assertEqual(decoded, release_set)


if __name__ == "__main__":
    unittest.main(verbosity=2)

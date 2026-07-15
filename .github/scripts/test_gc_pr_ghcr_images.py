#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gc_pr_ghcr_images import deletable_version_ids  # noqa: E402


class PrGhcrGcTest(unittest.TestCase):
    def test_deletes_only_versions_exclusively_tagged_for_pr(self):
        versions = [
            {
                "id": 1,
                "metadata": {
                    "container": {
                        "tags": ["pr-1190-latest", "pr-1190-deadbeef1234"]
                    }
                },
            },
            {
                "id": 2,
                "metadata": {
                    "container": {
                        "tags": ["pr-1190-deadbeef1234", "develop-latest"]
                    }
                },
            },
            {
                "id": 3,
                "metadata": {"container": {"tags": ["pr-1189-latest"]}},
            },
            {"id": 4, "metadata": {"container": {"tags": []}}},
        ]
        self.assertEqual(deletable_version_ids(versions, "pr-1190-"), [1])


if __name__ == "__main__":
    unittest.main(verbosity=2)

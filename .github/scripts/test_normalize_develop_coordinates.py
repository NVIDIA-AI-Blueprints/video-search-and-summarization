#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from normalize_develop_coordinates import (  # noqa: E402
    DEFAULT_REGISTRY,
    DEFAULT_TAG,
)
from update_pr_ghcr_candidates import update_container_defaults  # noqa: E402


class NormalizeDevelopCoordinatesTest(unittest.TestCase):
    def test_restores_develop_defaults_after_pr_tag_merge(self):
        original = (
            'VSS_CONTAINER_REGISTRY="${VSS_CONTAINER_REGISTRY:-'
            'ghcr.io/nvidia-ai-blueprints/vss}"\n'
            'VSS_CONTAINER_TAG="${VSS_CONTAINER_TAG:-pr-1190-deadbeef}"\n'
        )
        updated = update_container_defaults(
            original, DEFAULT_REGISTRY, DEFAULT_TAG
        )
        self.assertIn(
            'VSS_CONTAINER_TAG="${VSS_CONTAINER_TAG:-develop-latest}"',
            updated,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ghcr_image_guard.py (pure decision logic; the registry I/O
lives in check_container_tag_source.read_image_manifest_labels, tested via its
own gate). Run directly:

    python3 .github/scripts/test_ghcr_image_guard.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_container_tag_source import ImageManifestLabels  # noqa: E402
from ghcr_image_guard import preflight_decision, verify_decision  # noqa: E402

TREE = "a" * 40
OTHER_TREE = "b" * 40


def labels(tree=TREE, path="services/agent", name="vss-agent"):
    return ImageManifestLabels(source_tree_sha=tree, source_path=path, image_name=name)


class PreflightTest(unittest.TestCase):
    def test_missing_tag_404_builds(self):
        action, _ = preflight_decision(
            None, "index fetch failed: GET https://x returned 404: Not Found", TREE
        )
        self.assertEqual(action, "build")

    def test_network_error_fails_closed(self):
        action, message = preflight_decision(
            None, "index fetch failed: network error fetching https://x", TREE
        )
        self.assertEqual(action, "fail")
        self.assertIn("cannot prove", message)

    def test_same_content_rerun_skips(self):
        action, _ = preflight_decision(labels(), None, TREE)
        self.assertEqual(action, "skip")

    def test_different_content_fails(self):
        action, message = preflight_decision(labels(tree=OTHER_TREE), None, TREE)
        self.assertEqual(action, "fail")
        self.assertIn("immutable", message)

    def test_existing_unlabelled_tag_fails(self):
        action, _ = preflight_decision(
            None, "image config has no com.nvidia.vss.source_tree_sha label", TREE
        )
        self.assertEqual(action, "fail")


class VerifyTest(unittest.TestCase):
    KWARGS = dict(
        expected_tree_sha=TREE,
        expected_source_path="services/agent",
        expected_image_name="vss-agent",
    )

    def test_matching_labels_pass(self):
        ok, message = verify_decision(labels(), None, **self.KWARGS)
        self.assertTrue(ok)
        self.assertIn("gate will accept", message)

    def test_commit_sha_instead_of_tree_sha_fails(self):
        # The historical P0: stamping github.sha (a commit SHA) instead of the
        # tree hash. Both are 40 hex chars, so only value comparison catches it.
        ok, message = verify_decision(labels(tree=OTHER_TREE), None, **self.KWARGS)
        self.assertFalse(ok)
        self.assertIn("TREE hash", message)

    def test_wrong_source_path_fails(self):
        ok, message = verify_decision(labels(path="services/ui"), None, **self.KWARGS)
        self.assertFalse(ok)
        self.assertIn("source_path", message)

    def test_wrong_image_name_fails(self):
        ok, message = verify_decision(labels(name="vss-agent-ui"), None, **self.KWARGS)
        self.assertFalse(ok)
        self.assertIn("image_name", message)

    def test_unreadable_labels_fail(self):
        ok, message = verify_decision(None, "boom", **self.KWARGS)
        self.assertFalse(ok)
        self.assertIn("boom", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)

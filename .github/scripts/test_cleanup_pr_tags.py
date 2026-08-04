#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cleanup_pr_tags import (  # noqa: E402
    ghcr_packages,
    is_deletable,
    plan_deletions,
    pr_tag_pattern,
)

SHA = "abc123abc123"
OTHER_SHA = "def456def456"


def version(vid: int, tags: list[str]) -> dict:
    return {"id": vid, "metadata": {"container": {"tags": tags}}}


class TagPatternTest(unittest.TestCase):
    def test_matches_candidate_and_latest(self):
        pattern = pr_tag_pattern(1234)
        self.assertTrue(pattern.fullmatch(f"pr-1234-{SHA}"))
        self.assertTrue(pattern.fullmatch("pr-1234-latest"))

    def test_does_not_match_other_pr(self):
        pattern = pr_tag_pattern(1234)
        self.assertIsNone(pattern.fullmatch(f"pr-12345-{SHA}"))
        self.assertIsNone(pattern.fullmatch(f"pr-999-{SHA}"))

    def test_does_not_match_develop_or_tree_tags(self):
        pattern = pr_tag_pattern(1234)
        self.assertIsNone(pattern.fullmatch(f"develop-{SHA}"))
        self.assertIsNone(pattern.fullmatch("develop-latest"))
        self.assertIsNone(pattern.fullmatch("tree-" + "a" * 40))


class DeletableTest(unittest.TestCase):
    def setUp(self):
        self.pattern = pr_tag_pattern(1234)

    def test_pr_only_version_is_deletable(self):
        ok, _ = is_deletable([f"pr-1234-{SHA}", "pr-1234-latest"], self.pattern)
        self.assertTrue(ok)

    def test_shared_with_develop_tag_is_kept(self):
        ok, reason = is_deletable(
            [f"pr-1234-{SHA}", f"develop-{OTHER_SHA}"], self.pattern
        )
        self.assertFalse(ok)
        self.assertIn("shared digest", reason)

    def test_shared_with_content_tag_is_kept(self):
        ok, reason = is_deletable(
            [f"pr-1234-{SHA}", "tree-" + "a" * 40], self.pattern
        )
        self.assertFalse(ok)
        self.assertIn("shared digest", reason)

    def test_shared_with_other_pr_is_kept(self):
        ok, _ = is_deletable([f"pr-1234-{SHA}", f"pr-999-{SHA}"], self.pattern)
        self.assertFalse(ok)

    def test_untagged_version_is_left_alone(self):
        ok, reason = is_deletable([], self.pattern)
        self.assertFalse(ok)
        self.assertIn("untagged", reason)

    def test_foreign_only_version_is_untouched(self):
        ok, reason = is_deletable([f"develop-{SHA}"], self.pattern)
        self.assertFalse(ok)
        self.assertIn("no tag for this PR", reason)


class InventoryTest(unittest.TestCase):
    def test_only_ghcr_build_images_are_scanned(self):
        inventory = {
            "images": [
                {"name": "vss-agent", "ghcr_build": True},
                {"name": "vss-rt-cv", "ghcr_build": True},
                {"name": "vss-configurator", "ghcr_build": False},
                {"name": "no-flag"},
            ]
        }
        self.assertEqual(
            ghcr_packages(inventory), ["vss/vss-agent", "vss/vss-rt-cv"]
        )


class PlanDeletionsTest(unittest.TestCase):
    def test_plan_separates_deletable_from_shared(self):
        payload = [
            version(1, [f"pr-1234-{SHA}", "pr-1234-latest"]),
            version(2, [f"pr-1234-{OTHER_SHA}", f"develop-{OTHER_SHA}"]),
            version(3, [f"develop-{SHA}"]),
            version(4, []),
        ]
        to_delete, skipped = plan_deletions(
            "NVIDIA-AI-Blueprints", "vss/vss-agent", 1234, lambda *_: payload
        )
        self.assertEqual([vid for vid, _ in to_delete], [1])
        self.assertEqual(len(skipped), 1)
        self.assertIn("develop-", skipped[0][0])

    def test_missing_package_yields_empty_plan(self):
        to_delete, skipped = plan_deletions(
            "NVIDIA-AI-Blueprints", "vss/absent", 1234, lambda *_: None
        )
        self.assertEqual(to_delete, [])
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

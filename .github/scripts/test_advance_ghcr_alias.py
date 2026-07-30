#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from advance_ghcr_alias import advance, alias_plan, verify_tree_shas  # noqa: E402

DIGEST = "sha256:" + "1" * 64
MIRROR_DIGEST = "sha256:" + "2" * 64
REUSE_DIGEST = "sha256:" + "3" * 64
TREE = "a" * 40
OTHER_TREE = "b" * 40


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
                "source_path": "services/agent",
                "source_tree_sha": TREE,
            },
            {
                # mirror: lives in GHCR, no repo source to compare against
                "name": "vss-rt-cv",
                "strategy": "mirror",
                "image": "ghcr.io/nvidia-ai-blueprints/vss/vss-rt-cv",
                "tag": "develop-deadbeef1234",
                "digest": MIRROR_DIGEST,
                "upstream_digest": "sha256:" + "9" * 64,
            },
            {
                # reuse-pinned but resolved in GHCR: must still be retagged, or
                # the SHA-derived coordinate set is incomplete at this commit
                "name": "vss-alert-ms",
                "strategy": "reuse-pinned",
                "image": "ghcr.io/nvidia-ai-blueprints/vss/vss-alert-ms",
                "tag": "develop-cafebabe5678",
                "digest": REUSE_DIGEST,
            },
            {
                # external pin at nvcr.io — cannot be retagged, stays derivable from git
                "name": "vss-configurator",
                "strategy": "reuse-pinned",
                "image": "nvcr.io/nvidia/vss-core/vss-configurator",
                "tag": "3.2.1",
                "digest": None,
            },
        ],
    }


class AliasPlanTest(unittest.TestCase):
    def test_plan_covers_every_resolved_ghcr_entry(self):
        plan = alias_plan(release_set(), "develop-latest")
        self.assertEqual(
            [item.name for item in plan],
            ["vss-agent", "vss-alert-ms", "vss-rt-cv"],
        )

    def test_mirror_and_reuse_pinned_are_no_longer_skipped(self):
        names = {item.name for item in alias_plan(release_set(), "develop-latest")}
        self.assertIn("vss-rt-cv", names)
        self.assertIn("vss-alert-ms", names)

    def test_non_ghcr_pin_is_excluded(self):
        names = {item.name for item in alias_plan(release_set(), "develop-latest")}
        self.assertNotIn("vss-configurator", names)

    def test_entry_without_digest_is_excluded(self):
        data = release_set()
        data["images"][1]["digest"] = None
        names = {item.name for item in alias_plan(data, "develop-latest")}
        self.assertNotIn("vss-rt-cv", names)

    def test_sha_alias_targets_every_image(self):
        plan = alias_plan(release_set(), "develop-deadbeef1234")
        self.assertTrue(
            all(item.target.endswith(":develop-deadbeef1234") for item in plan)
        )

    def test_non_develop_release_set_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "only from develop"):
            alias_plan(release_set("pull-request/1190"), "develop-validated")

    def test_empty_plan_is_rejected(self):
        data = release_set()
        data["images"] = [data["images"][-1]]
        with self.assertRaisesRegex(ValueError, "no retaggable GHCR entries"):
            alias_plan(data, "develop-latest")


class TreeShaGateTest(unittest.TestCase):
    def test_matching_tree_sha_passes(self):
        report = verify_tree_shas(
            release_set(), Path("/repo"), "abc123", lambda *_: TREE
        )
        self.assertTrue(any("vss-agent" in line and "matches" in line for line in report))

    def test_mismatched_tree_sha_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "not built from this commit"):
            verify_tree_shas(
                release_set(), Path("/repo"), "abc123", lambda *_: OTHER_TREE
            )

    def test_unresolvable_source_path_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "does not resolve to a tree"):
            verify_tree_shas(
                release_set(), Path("/repo"), "abc123", lambda *_: None
            )

    def test_entry_without_source_tree_sha_is_unverified_not_failed(self):
        report = verify_tree_shas(
            release_set(), Path("/repo"), "abc123", lambda *_: TREE
        )
        self.assertTrue(
            any("vss-rt-cv" in line and "unverified" in line for line in report)
        )

    def test_non_ghcr_entries_are_not_gated(self):
        report = verify_tree_shas(
            release_set(), Path("/repo"), "abc123", lambda *_: TREE
        )
        self.assertFalse(any("vss-configurator" in line for line in report))


class AdvanceTest(unittest.TestCase):
    def test_advance_verifies_alias_digest(self):
        update = alias_plan(release_set(), "develop-validated")[0]
        commands: list[list[str]] = []

        def runner(command: list[str]) -> str:
            commands.append(command)
            return json.dumps({"digest": update.digest}) if "inspect" in command else ""

        advance(update, runner)
        self.assertEqual(commands[0][3], "create")
        self.assertEqual(commands[1][3], "inspect")

    def test_advance_rejects_digest_drift(self):
        update = alias_plan(release_set(), "develop-validated")[0]

        def runner(command: list[str]) -> str:
            return json.dumps({"digest": MIRROR_DIGEST}) if "inspect" in command else ""

        with self.assertRaisesRegex(RuntimeError, "alias digest"):
            advance(update, runner)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from container_build_plan import source_tree_hash  # noqa: E402
from advance_ghcr_alias import (  # noqa: E402
    advance,
    alias_plan,
    tree_sources,
    verify_tree_shas,
)

DIGEST = "sha256:" + "1" * 64
# Every GHCR entry in the real inventory carries a source_path, so every one
# has a content tag. A GHCR entry without one cannot exist in practice; the
# refusal path is covered explicitly by test_missing_content_tag_refuses_to_plan.
ALL_CONTENT = {
    "vss-agent": "tree-" + "a" * 40,
    "vss-rt-cv": "tree-" + "c" * 40,
    "vss-alert-ms": "tree-" + "d" * 40,
}
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
        plan = alias_plan(release_set(), "develop-latest", ALL_CONTENT)
        self.assertEqual(
            [item.name for item in plan],
            ["vss-agent", "vss-alert-ms", "vss-rt-cv"],
        )

    def test_mirror_and_reuse_pinned_are_no_longer_skipped(self):
        names = {item.name for item in alias_plan(release_set(), "develop-latest", ALL_CONTENT)}
        self.assertIn("vss-rt-cv", names)
        self.assertIn("vss-alert-ms", names)

    def test_non_ghcr_pin_is_excluded(self):
        names = {item.name for item in alias_plan(release_set(), "develop-latest", ALL_CONTENT)}
        self.assertNotIn("vss-configurator", names)

    def test_non_ghcr_entry_without_digest_stays_excluded(self):
        data = release_set()
        names = {item.name for item in alias_plan(data, "develop-latest", ALL_CONTENT)}
        self.assertNotIn("vss-configurator", names)

    def test_sha_alias_targets_every_image(self):
        plan = alias_plan(release_set(), "develop-deadbeef1234", ALL_CONTENT)
        self.assertTrue(
            all(item.target.endswith(":develop-deadbeef1234") for item in plan)
        )

    def test_pull_request_ref_is_accepted(self):
        """A PR branch publishes pr-<N>-* and needs the same complete coverage."""
        plan = alias_plan(release_set("pull-request/1190"), "pr-1190-abc123abc123", ALL_CONTENT)
        self.assertEqual(len(plan), 3)
        self.assertTrue(
            all(item.target.endswith(":pr-1190-abc123abc123") for item in plan)
        )

    def test_unexpected_ref_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "expected develop or pull-request"):
            alias_plan(release_set("refs/heads/random"), "develop-validated")

    def test_malformed_pull_request_ref_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "expected develop or pull-request"):
            alias_plan(release_set("pull-request/abc"), "develop-validated")

    def test_empty_plan_is_rejected(self):
        data = release_set()
        data["images"] = [data["images"][-1]]
        with self.assertRaisesRegex(ValueError, "no retaggable GHCR entries"):
            alias_plan(data, "develop-latest", ALL_CONTENT)


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
        with self.assertRaisesRegex(RuntimeError, "does not resolve to source content"):
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
    def _update(self):
        return alias_plan(release_set(), "develop-abc123abc123", ALL_CONTENT)[0]

    def test_creates_from_the_content_tag_then_verifies(self):
        commands: list[list[str]] = []

        def runner(command: list[str]) -> str:
            commands.append(command)
            return json.dumps({"digest": DIGEST}) if "inspect" in command else ""

        advance(self._update(), runner)
        self.assertEqual(commands[0][3], "create")
        self.assertTrue(commands[0][-1].endswith(f":tree-{TREE}"))
        self.assertEqual(commands[1][3], "inspect")

    def test_advance_rejects_digest_drift(self):
        def runner(command: list[str]) -> str:
            return json.dumps({"digest": MIRROR_DIGEST}) if "inspect" in command else ""

        with self.assertRaisesRegex(RuntimeError, "alias digest"):
            advance(self._update(), runner)

    def test_digestless_entry_reports_the_resolved_digest(self):
        """Reuse-pinned entries record no digest; the source was content
        addressed, so there is nothing to assert against."""
        data = release_set()
        for image in data["images"]:
            if image["image"].startswith("ghcr.io/"):
                image["digest"] = None
        update = alias_plan(data, "develop-abc123abc123", ALL_CONTENT)[0]

        def runner(command: list[str]) -> str:
            return json.dumps({"digest": DIGEST}) if "inspect" in command else ""

        advance(update, runner)  # must not raise


class TreeSourceTest(unittest.TestCase):
    """tree-<sha> is content-addressed: by definition the image built from
    this commit's source, so it is correct where develop-latest was only
    incidentally correct -- and stays correct on a PR branch."""

    def _digestless(self):
        data = release_set()
        for image in data["images"]:
            if image["image"].startswith("ghcr.io/"):
                image.update(digest=None, tag="develop-latest")
        return data

    def test_entries_with_repo_source_get_a_content_tag(self):
        found = tree_sources(
            self._digestless(), Path("/repo"), "abc123", lambda *_: TREE
        )
        self.assertEqual(found.get("vss-agent"), f"tree-{TREE}")

    def test_mirror_entry_without_source_path_has_none(self):
        found = tree_sources(
            self._digestless(), Path("/repo"), "abc123", lambda *_: TREE
        )
        self.assertNotIn("vss-rt-cv", found)  # mirror: no source_path

    def test_plan_prefers_the_content_tag_over_the_moving_alias(self):
        data = self._digestless()
        plan = {i.name: i.source for i in alias_plan(data, "develop-abc123abc123", ALL_CONTENT)}
        self.assertTrue(plan["vss-agent"].endswith(":" + ALL_CONTENT["vss-agent"]))

    def test_content_tag_is_the_only_source_even_when_a_digest_exists(self):
        """One source rule, no tiers: a fresh build's content tag and its
        candidate tag are the same manifest, so there is nothing to choose."""
        data = release_set()  # vss-agent has a real digest
        plan = {i.name: i.source for i in alias_plan(data, "develop-latest", ALL_CONTENT)}
        self.assertTrue(plan["vss-agent"].endswith(":" + ALL_CONTENT["vss-agent"]))


    def test_multi_source_entry_gets_combined_content_tag(self):
        data = self._digestless()
        data["images"][0]["source_paths"] = ["services/agent", "libs/shared"]
        mapping = {"services/agent": TREE, "libs/shared": OTHER_TREE}

        def reader(_repo: Path, _commit: str, source_path: str) -> str | None:
            return mapping.get(source_path)

        expected = source_tree_hash(
            Path("/repo"),
            "abc123",
            ["services/agent", "libs/shared"],
            tree_reader=reader,
        )
        found = tree_sources(data, Path("/repo"), "abc123", reader)
        self.assertEqual(found.get("vss-agent"), f"tree-{expected}")

    def test_missing_content_tag_refuses_to_plan(self):
        """No fallback: a reference that may not describe this commit is worse
        than a failed run."""
        with self.assertRaisesRegex(ValueError, "no content tag"):
            alias_plan(release_set(), "develop-latest", {})

if __name__ == "__main__":
    unittest.main(verbosity=2)

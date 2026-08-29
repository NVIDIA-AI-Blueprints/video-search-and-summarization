#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cleanup_pr_tags import (  # noqa: E402
    PER_PAGE,
    detachable_tags,
    ghcr_packages,
    is_deletable,
    iter_versions,
    plan_deletions,
    plan_detach,
    pr_tag_pattern,
    tag_variants,
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


class DetachableTagsTest(unittest.TestCase):
    def setUp(self):
        self.pattern = pr_tag_pattern(1234)

    def test_shared_with_content_tag_is_detachable(self):
        self.assertEqual(
            detachable_tags(
                [f"pr-1234-{SHA}", "pr-1234-latest", "tree-" + "a" * 40], self.pattern
            ),
            [f"pr-1234-{SHA}", "pr-1234-latest"],
        )

    def test_deletable_version_needs_no_detach(self):
        # Every tag belongs to this PR, so the delete pass handles it directly.
        self.assertEqual(
            detachable_tags([f"pr-1234-{SHA}", "pr-1234-latest"], self.pattern), []
        )

    def test_foreign_only_version_is_untouched(self):
        self.assertEqual(
            detachable_tags([f"develop-{SHA}", "tree-" + "a" * 40], self.pattern), []
        )

    def test_other_prs_tags_are_never_detached(self):
        self.assertEqual(
            detachable_tags([f"pr-999-{SHA}", f"develop-{SHA}"], self.pattern), []
        )


class PaginationTest(unittest.TestCase):
    """These packages run to several pages — vss/vss-agent has 600 versions."""

    def _paged(self, pages: list[list[dict]]):
        seen: list[str] = []

        def requester(_method: str, url: str):
            seen.append(url)
            page = int(url.rsplit("page=", 1)[1])
            return pages[page - 1] if page <= len(pages) else []

        return requester, seen

    def test_follows_every_page(self):
        pages = [
            [version(i, [f"pr-1234-{SHA}"]) for i in range(PER_PAGE)],
            [version(1000 + i, [f"develop-{SHA}"]) for i in range(7)],
        ]
        requester, seen = self._paged(pages)
        got = iter_versions("NVIDIA-AI-Blueprints", "vss/vss-agent", requester)
        self.assertEqual(len(got), PER_PAGE + 7)
        self.assertEqual(len(seen), 2)

    def test_stops_on_short_page(self):
        requester, seen = self._paged([[version(1, [])]])
        iter_versions("NVIDIA-AI-Blueprints", "vss/vss-agent", requester)
        self.assertEqual(len(seen), 1)

    def test_detach_sees_tags_beyond_the_first_page(self):
        pages = [
            [version(i, [f"develop-{SHA}"]) for i in range(PER_PAGE)],
            [version(1000, [f"pr-1234-{OTHER_SHA}", "tree-" + "c" * 40])],
        ]
        requester, _ = self._paged(pages)
        self.assertEqual(
            plan_detach("NVIDIA-AI-Blueprints", "vss/vss-agent", 1234, requester),
            [f"pr-1234-{OTHER_SHA}"],
        )


class PlanDetachTest(unittest.TestCase):
    def test_collects_tags_from_shared_versions_only(self):
        payload = [
            version(1, [f"pr-1234-{SHA}", "pr-1234-latest"]),  # deletable outright
            version(2, [f"pr-1234-{OTHER_SHA}", "tree-" + "b" * 40]),  # shared
            version(3, [f"develop-{SHA}"]),
            version(4, []),
        ]
        self.assertEqual(
            plan_detach(
                "NVIDIA-AI-Blueprints", "vss/vss-agent", 1234, lambda *_: payload
            ),
            [f"pr-1234-{OTHER_SHA}"],
        )

    def test_missing_package_yields_empty_plan(self):
        self.assertEqual(
            plan_detach("NVIDIA-AI-Blueprints", "vss/absent", 1234, lambda *_: None), []
        )



class TagVariantTest(unittest.TestCase):
    """Variant and platform suffixes must be owned, and nothing else.

    929 of 1886 ``pr-*`` tags in GHCR were unreachable by this cleanup because
    the pattern knew about neither. The large class was ``-sbsa`` (879), not
    the architecture suffixes -- so a fix aimed only at ``-amd64``/``-arm64``
    would have recovered a twentieth of the leak.
    """

    SUFFIXES = ("sbsa",)
    PLATFORMS = ("amd64", "arm64")

    def pattern(self, pr=1234):
        return pr_tag_pattern(pr, self.SUFFIXES, self.PLATFORMS)

    def test_owns_every_shape_the_build_publishes(self):
        for tag in (
            "pr-1234-abc123abc123",
            "pr-1234-latest",
            "pr-1234-abc123abc123-amd64",
            "pr-1234-abc123abc123-arm64",
            "pr-1234-abc123abc123-sbsa",
            "pr-1234-latest-sbsa",
            "pr-1234-abc123abc123-sbsa-arm64",
        ):
            self.assertRegex(tag, self.pattern(), tag)

    def test_never_owns_a_foreign_tag(self):
        # The asymmetry that governs this pattern: claiming a foreign tag can
        # delete a live develop image, which needs a rebuild to recover. Missing
        # a shape only leaks storage. So over-claiming must be impossible.
        for tag in (
            "pr-9999-abc123abc123",            # another PR
            "pr-9999-abc123abc123-amd64",
            "pr-12345-abc123abc123",           # prefix overlap with 1234
            "develop-abc123abc123",
            "develop-abc123abc123-amd64",
            "develop-latest",
            "tree-" + "a" * 40,
            "tree-" + "a" * 40 + "-sbsa",
            "latest",
            "xpr-1234-abc123abc123",
        ):
            self.assertIsNone(self.pattern().fullmatch(tag), tag)

    def test_platform_suffix_only_follows_a_sha_candidate(self):
        """`pr-<N>-latest-amd64` is not a shape this repository can produce.

        A moving alias is advanced onto the MERGED manifest, never onto a
        per-architecture staging image -- confirmed against GHCR, where zero
        `latest-<arch>` tags exist. Owning it would widen deletion past the
        build's own grammar for no gain, and the entire argument for
        enumerating these suffixes is that ownership must not outrun what the
        build actually publishes. Raised by review on #1894.
        """
        for tag in (
            "pr-1234-latest-amd64",
            "pr-1234-latest-arm64",
            "pr-1234-latest-sbsa-arm64",
        ):
            self.assertIsNone(self.pattern().fullmatch(tag), tag)

        # the variant suffix DOES follow latest, and must keep working
        self.assertRegex("pr-1234-latest-sbsa", self.pattern())

    def test_rejects_suffixes_the_inventory_does_not_declare(self):
        # Enumerated, not wildcarded. An unknown platform or a trailing extra
        # segment is somebody else's tag until the inventory says otherwise.
        for tag in (
            "pr-1234-abc123abc123-x86",
            "pr-1234-abc123abc123-riscv64",
            "pr-1234-abc123abc123-sbsa-amd64-extra",
            "pr-1234-abc123abc12",             # 11 hex, not 12
        ):
            self.assertIsNone(self.pattern().fullmatch(tag), tag)

    def test_default_arguments_keep_the_narrow_behaviour(self):
        # A caller that passes no variants must not become more aggressive.
        narrow = pr_tag_pattern(1234)
        self.assertRegex("pr-1234-abc123abc123", narrow)
        self.assertIsNone(narrow.fullmatch("pr-1234-abc123abc123-amd64"))

    def test_variants_are_read_from_the_inventory(self):
        inventory = {
            "images": [
                {"name": "a", "ghcr_build": True,
                 "platforms": ["linux/amd64", "linux/arm64"]},
                {"name": "b", "ghcr_build": True, "tag_suffix": "-sbsa",
                 "platforms": ["linux/arm64"]},
                # not a GHCR build: must not contribute a suffix
                {"name": "c", "tag_suffix": "-ignored",
                 "platforms": ["linux/s390x"]},
            ]
        }
        suffixes, platforms = tag_variants(inventory)
        self.assertEqual(suffixes, ["sbsa"])
        self.assertEqual(platforms, ["amd64", "arm64"])

    def test_a_staging_tag_alone_makes_its_version_deletable(self):
        # Before the fix this version was kept forever: its only tag read as
        # foreign, so is_deletable refused it.
        deletable, _ = is_deletable(
            ["pr-1234-abc123abc123-amd64"], self.pattern()
        )
        self.assertTrue(deletable)

    def test_a_staging_tag_beside_a_develop_tag_is_still_kept(self):
        # Widening ownership must not weaken the shared-digest rule.
        deletable, reason = is_deletable(
            ["pr-1234-abc123abc123-amd64", "develop-def456def456"], self.pattern()
        )
        self.assertFalse(deletable, reason)

if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("update_pr_ghcr_candidates.py")
SPEC = importlib.util.spec_from_file_location("update_pr_ghcr_candidates", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class CandidateCommentTest(unittest.TestCase):
    def test_pr_number_only_accepts_synthetic_ref(self):
        self.assertEqual(module.pr_number("pull-request/1190"), 1190)
        self.assertIsNone(module.pr_number("develop"))
        self.assertIsNone(module.pr_number("pull-request/not-a-number"))

    def test_select_release_set_run_requires_exact_ref_sha_and_success(self):
        runs = [
            {
                "id": 1,
                "head_sha": "a" * 40,
                "head_branch": "pull-request/1190",
                "conclusion": "failure",
            },
            {
                "id": 2,
                "head_sha": "a" * 40,
                "head_branch": "pull-request/1190",
                "conclusion": "success",
            },
        ]
        self.assertEqual(
            module.select_release_set_run(
                runs, "a" * 40, "pull-request/1190"
            )["id"],
            2,
        )

    def test_comment_lists_only_immutable_ghcr_builds(self):
        release_set = {
            "release_set_id": "sha256:" + "1" * 64,
            "images": [
                {
                    "name": "vss-agent",
                    "strategy": "build",
                    "image": "ghcr.io/nvidia-ai-blueprints/vss/vss-agent",
                    "tag": "pr-1190-deadbeef",
                    "digest": "sha256:" + "2" * 64,
                },
                {
                    "name": "vss-configurator",
                    "strategy": "reuse-pinned",
                    "image": "nvcr.io/nvidia/vss-core/vss-configurator",
                    "tag": "3.2.1",
                    "digest": None,
                },
            ],
        }
        body = module.render_comment(release_set, "a" * 40)
        self.assertIn(module.MARKER, body)
        self.assertIn("ghcr.io/nvidia-ai-blueprints/vss/vss-agent", body)
        self.assertIn("pr-1190-latest", body)
        self.assertNotIn("vss-configurator", body)
        self.assertIn("does not rebuild", body)

    def test_moving_alias_derives_from_immutable_tag(self):
        self.assertEqual(module.moving_alias("develop-deadbeef"), "develop-latest")
        self.assertEqual(
            module.moving_alias("pr-1190-deadbeef"), "pr-1190-latest"
        )
        self.assertEqual(module.moving_alias("release-3.2.0"), "")

    def test_upsert_comment_finds_marker_after_first_page(self):
        class FakeApi:
            def __init__(self):
                self.calls = []

            def request(self, method, path, payload=None):
                self.calls.append((method, path, payload))
                if method == "GET" and path.endswith("&page=1"):
                    return [{"id": index, "body": "other"} for index in range(100)]
                if method == "GET" and path.endswith("&page=2"):
                    return [{"id": 999, "body": module.MARKER}]
                return {}

        api = FakeApi()
        module.upsert_comment(api, "org/repo", 1190, "updated")
        self.assertIn(
            (
                "PATCH",
                "/repos/org/repo/issues/comments/999",
                {"body": "updated"},
            ),
            api.calls,
        )
        self.assertFalse(any(method == "POST" for method, _, _ in api.calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for persistent Fern docs branch routing."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from select_fern_docs_target import select_target  # noqa: E402


class SelectTargetTest(unittest.TestCase):
    def test_develop_uses_its_own_site(self):
        self.assertEqual(select_target("develop", ["release/99.0.0"]), {
            "publish": "true",
            "instance": "nvidia-vss-develop.docs.buildwithfern.com/vss",
        })

    def test_release_versions_are_compared_numerically(self):
        branches = ["release/3.9.9", "release/3.10.0", "release/3.3.12"]
        self.assertEqual(select_target("release/3.10.0", branches), {
            "publish": "true",
            "instance": "nvidia-vss-staging.docs.buildwithfern.com/vss",
        })
        self.assertEqual(select_target("release/3.9.9", branches), {"publish": "false"})

    def test_each_version_component_has_numeric_precedence(self):
        for branches in (
            ["release/3.99.99", "release/4.0.0"],
            ["release/3.3.9", "release/3.3.10"],
        ):
            with self.subTest(branches=branches):
                self.assertEqual(select_target(branches[-1], branches)["publish"], "true")
                self.assertEqual(select_target(branches[0], branches)["publish"], "false")

    def test_non_release_and_non_stable_names_are_skipped(self):
        invalid = [
            "main", "feature/docs", "release/3.4", "release/v3.4.0",
            "release/3.4.0-rc.1", "release/3.4.0+build.1", "release/03.4.0",
            "release/3.04.0", "release/3.4.00", "release/3.4.0/extra",
        ]
        branches = ["release/3.3.0", *invalid]
        self.assertEqual(select_target("release/3.3.0", branches)["publish"], "true")
        for branch in invalid:
            with self.subTest(branch=branch):
                self.assertEqual(select_target(branch, branches), {"publish": "false"})

    def test_absent_or_deleted_release_is_skipped(self):
        self.assertEqual(select_target("release/3.3.0", []), {"publish": "false"})
        self.assertEqual(select_target("release/3.4.0", ["release/3.3.0"]), {"publish": "false"})
        self.assertEqual(select_target("develop", [])["publish"], "true")

    def test_cli_emits_github_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            branches = root / "branches"
            output = root / "output"
            branches.write_text("release/3.9.0\nrelease/3.10.0\n")
            subprocess.run([
                sys.executable, str(Path(__file__).with_name("select_fern_docs_target.py")),
                "--branch", "release/3.10.0", "--branches-file", str(branches),
                "--output", str(output),
            ], check=True, capture_output=True)
            values = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertEqual(values, select_target("release/3.10.0", branches.read_text().splitlines()))


if __name__ == "__main__":
    unittest.main()

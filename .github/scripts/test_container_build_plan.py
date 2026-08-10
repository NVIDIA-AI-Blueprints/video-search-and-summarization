#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from container_build_plan import (  # noqa: E402
    candidate_coordinates,
    inspect_manifest,
    parse_source_paths,
    source_tree_hash,
    validate_manifest,
)

DIGEST = "sha256:" + "1" * 64
COMMIT = "a" * 40
TREE = "b" * 40


class CandidateCoordinatesTest(unittest.TestCase):
    def test_pr_coordinates(self):
        result = candidate_coordinates(
            ref_name="pull-request/1190",
            commit_sha=COMMIT,
            owner="NVIDIA-AI-Blueprints",
            image_name="vss-agent",
            tree_sha=TREE,
        )
        self.assertEqual(
            result.image,
            "ghcr.io/nvidia-ai-blueprints/vss/vss-agent",
        )
        self.assertEqual(result.tag, "pr-1190-" + "a" * 12)
        self.assertEqual(result.content_tag, "tree-" + TREE)

    def test_develop_coordinates(self):
        result = candidate_coordinates(
            ref_name="develop",
            commit_sha=COMMIT,
            owner="NVIDIA-AI-Blueprints",
            image_name="vss-agent-ui",
            tree_sha=TREE,
        )
        self.assertEqual(result.tag, "develop-" + "a" * 12)


class SourceHashTest(unittest.TestCase):
    def git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.stdout.strip()

    def make_repo(self, tmp: str) -> Path:
        repo = Path(tmp)
        self.git(repo, "init", "-q", "-b", "develop")
        self.git(repo, "config", "user.email", "test@test")
        self.git(repo, "config", "user.name", "test")
        for rel in ("services/app/main.py", "libs/shared/lib.py"):
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("v1\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "initial")
        return repo

    def test_single_source_returns_git_tree_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(tmp)
            expected = self.git(repo, "rev-parse", "HEAD:services/app")
            self.assertEqual(
                source_tree_hash(repo, "HEAD", ["services/app"]), expected
            )

    def test_multi_source_hash_is_order_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(tmp)
            left = source_tree_hash(
                repo, "HEAD", ["services/app", "libs/shared"]
            )
            right = source_tree_hash(
                repo, "HEAD", ["libs/shared", "services/app"]
            )
            self.assertEqual(left, right)
            self.assertRegex(left, r"^[0-9a-f]{40}$")

    def test_parse_source_paths_strips_empty_items_and_duplicates(self):
        self.assertEqual(
            parse_source_paths(" services/app,libs/shared,,services/app/ "),
            ["services/app", "libs/shared"],
        )


class ManifestValidationTest(unittest.TestCase):
    def test_exact_platforms_ignore_attestations(self):
        evidence = validate_manifest(
            {
                "digest": DIGEST,
                "manifests": [
                    {"platform": {"os": "linux", "architecture": "arm64"}},
                    {"platform": {"os": "unknown", "architecture": "unknown"}},
                    {"platform": {"os": "linux", "architecture": "amd64"}},
                ],
            },
            ["linux/amd64", "linux/arm64"],
        )
        self.assertEqual(evidence.digest, DIGEST)
        self.assertEqual(
            evidence.platforms, ("linux/amd64", "linux/arm64")
        )

    def test_extra_platform_fails(self):
        with self.assertRaisesRegex(ValueError, "does not match inventory"):
            validate_manifest(
                {
                    "digest": DIGEST,
                    "manifests": [
                        {"platform": {"os": "linux", "architecture": "amd64"}},
                        {"platform": {"os": "linux", "architecture": "arm64"}},
                    ],
                },
                ["linux/amd64"],
            )

    def test_registry_runner_is_injected(self):
        commands: list[list[str]] = []

        def runner(command: list[str]) -> str:
            commands.append(command)
            return json.dumps({"digest": DIGEST, "manifests": []})

        manifest = inspect_manifest("ghcr.io/org/vss/image:tag", runner)
        self.assertEqual(manifest["digest"], DIGEST)
        self.assertEqual(commands[0][0:4], ["docker", "buildx", "imagetools", "inspect"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

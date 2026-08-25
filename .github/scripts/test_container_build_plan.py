#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from container_build_plan import (  # noqa: E402
    candidate_coordinates,
    inspect_manifest,
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

    def test_variant_uses_shared_repository_and_suffixes_all_tags(self):
        result = candidate_coordinates(
            ref_name="develop",
            commit_sha=COMMIT,
            owner="NVIDIA-AI-Blueprints",
            image_name="vss-rt-cv",
            tree_sha=TREE,
            tag_suffix="-sbsa",
        )
        self.assertEqual(
            result.image,
            "ghcr.io/nvidia-ai-blueprints/vss/vss-rt-cv",
        )
        self.assertEqual(result.tag, "develop-" + "a" * 12 + "-sbsa")
        self.assertEqual(result.content_tag, "tree-" + TREE + "-sbsa")

    def test_variant_suffix_must_be_tag_safe_and_explicit(self):
        with self.assertRaisesRegex(ValueError, "tag suffix"):
            candidate_coordinates(
                ref_name="develop",
                commit_sha=COMMIT,
                owner="NVIDIA-AI-Blueprints",
                image_name="vss-rt-cv",
                tree_sha=TREE,
                tag_suffix="sbsa",
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


class WorkflowTagSuffixInvocationTest(unittest.TestCase):
    """A tag suffix starts with '-', so it must be passed as --tag-suffix=VALUE.

    With a space, argparse reads the leading '-' as the start of another option
    and dies with "argument --tag-suffix: expected one argument", failing every
    variant build before it starts.
    """

    def test_workflow_passes_tag_suffix_with_equals(self):
        workflow = (
            Path(__file__).resolve().parents[1] / "workflows" / "build-dev-images.yml"
        ).read_text()
        self.assertNotIn('--tag-suffix "', workflow)
        self.assertIn('--tag-suffix="', workflow)

    def test_leading_dash_suffix_parses_in_equals_form(self):
        import container_build_plan

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "github-output"
            out.touch()
            argv = [
                "container_build_plan.py", "metadata",
                "--ref-name", "pull-request/1", "--commit-sha", "0" * 40,
                "--owner", "NVIDIA-AI-Blueprints", "--image-name", "vss-rt-cv",
                "--tree-sha", "a" * 40, "--tag-suffix=-sbsa",
                "--github-output", str(out),
            ]
            with unittest.mock.patch.object(sys, "argv", argv):
                self.assertEqual(container_build_plan.main(), 0)
            written = out.read_text()

        self.assertIn("tag=pr-1-000000000000-sbsa", written)
        self.assertIn(f"content_tag=tree-{'a' * 40}-sbsa", written)


if __name__ == "__main__":
    unittest.main(verbosity=2)

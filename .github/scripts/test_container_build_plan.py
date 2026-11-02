#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import os
import re
import subprocess
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
    validate_legacy_provenance,
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

    def test_rebuild_coordinates_preserve_content_and_variant_aliases(self):
        base = dict(
            ref_name="develop",
            commit_sha=COMMIT,
            owner="NVIDIA-AI-Blueprints",
            image_name="vss-rt-cv",
            tree_sha=TREE,
            tag_suffix="-sbsa",
        )
        original = candidate_coordinates(**base)
        first = candidate_coordinates(**base, rebuild_id="123-1")
        rerun = candidate_coordinates(**base, rebuild_id="123-2")
        self.assertEqual(first.tag, "develop-" + COMMIT[:12] + "-rebuild-123-1-sbsa")
        self.assertEqual(first.content_tag, original.content_tag)
        self.assertNotEqual(first.tag, rerun.tag)
        self.assertNotEqual(first.tag, original.tag)
        with self.assertRaisesRegex(ValueError, "rebuild ID"):
            candidate_coordinates(**base, rebuild_id="bad/tag")


class LegacyProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.manifest = {"manifests": []}
        self.raw = {}
        for arch, digit in (("amd64", "1"), ("arm64", "2")):
            digest = "sha256:" + digit * 64
            attestation = "sha256:" + str(int(digit) + 2) * 64
            self.manifest["manifests"].extend(
                [
                    {
                        "digest": digest,
                        "platform": {"os": "linux", "architecture": arch},
                    },
                    {
                        "digest": attestation,
                        "platform": {"os": "unknown", "architecture": "unknown"},
                        "annotations": {
                            "vnd.docker.reference.type": "attestation-manifest",
                            "vnd.docker.reference.digest": digest,
                        },
                    },
                ]
            )
            self.raw[attestation] = {
                "layers": [
                    {
                        "mediaType": "application/vnd.in-toto+json",
                        "annotations": {
                            "in-toto.io/predicate-type": "https://slsa.dev/provenance/v0.2"
                        },
                    }
                ]
            }

    def runner(self, command):
        self.assertEqual(command[:4], ["docker", "buildx", "imagetools", "inspect"])
        self.assertEqual(command[-1], "--raw")
        self.assertTrue(command[-2].startswith("ghcr.io/org/vss/image@sha256:"))
        return json.dumps(self.raw[command[-2].split("@", 1)[1]])

    def verify(self):
        validate_legacy_provenance(
            "ghcr.io/org/vss/image:tag", self.manifest, self.runner
        )

    def test_both_platforms_keep_slsa(self):
        self.verify()

    def test_missing_platform_provenance_fails(self):
        self.manifest["manifests"].pop()
        with self.assertRaisesRegex(ValueError, "each runnable platform"):
            self.verify()

    def test_oci_referrer_fails(self):
        next(iter(self.raw.values()))["subject"] = {"digest": DIGEST}
        with self.assertRaisesRegex(ValueError, "OCI artifact referrer"):
            self.verify()

    def test_non_slsa_fails(self):
        next(iter(self.raw.values()))["layers"][0]["annotations"] = {}
        with self.assertRaisesRegex(ValueError, "no SLSA"):
            self.verify()

    def test_duplicate_fails(self):
        self.manifest["manifests"].append(self.manifest["manifests"][1])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.verify()


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
        self.assertEqual(evidence.platforms, ("linux/amd64", "linux/arm64"))

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
        self.assertEqual(
            commands[0][0:4], ["docker", "buildx", "imagetools", "inspect"]
        )


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
                "container_build_plan.py",
                "metadata",
                "--ref-name",
                "pull-request/1",
                "--commit-sha",
                "0" * 40,
                "--owner",
                "NVIDIA-AI-Blueprints",
                "--image-name",
                "vss-rt-cv",
                "--tree-sha",
                "a" * 40,
                "--tag-suffix=-sbsa",
                "--github-output",
                str(out),
            ]
            with unittest.mock.patch.object(sys, "argv", argv):
                self.assertEqual(container_build_plan.main(), 0)
            written = out.read_text()

        self.assertIn("tag=pr-1-000000000000-sbsa", written)
        self.assertIn(f"content_tag=tree-{'a' * 40}-sbsa", written)


class WorkflowRebuildPlanningTest(unittest.TestCase):
    def setUp(self):
        self.workflow = (
            Path(__file__).resolve().parents[1] / "workflows" / "build-dev-images.yml"
        ).read_text()

    def job(self, name):
        match = re.search(
            rf"^  {re.escape(name)}:\n.*?(?=^  [a-z][a-z-]*:\n|\Z)",
            self.workflow,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match)
        return match.group()

    def test_partial_retries_use_retained_planning_output_in_every_consumer(self):
        # A failed ARM64 job and its merge can run on attempt 2 while the
        # successful AMD64 job keeps attempt-1 tags. All consumers must use
        # the retained planning output, including final alias publication.
        self.assertNotIn("IMAGE_REBUILD_ID:", self.workflow.split("jobs:", 1)[0])
        planning = self.job("changes")
        self.assertIn("rebuild-id: ${{ steps.rebuild.outputs.id }}", planning)
        self.assertIn("github.run_attempt", planning)
        for name in (
            "build",
            "build-native-platforms",
            "publish-native-images",
            "assemble",
        ):
            with self.subTest(job=name):
                job = self.job(name)
                self.assertIn(
                    "IMAGE_REBUILD_ID: ${{ needs.changes.outputs.rebuild-id }}", job
                )
                self.assertNotIn("github.run_attempt", job)
                self.assertIn("$IMAGE_REBUILD_ID", job)

    def test_planning_emits_id_for_dispatch_and_empty_for_push(self):
        # Execute the actual output-writing command, including the empty-ID
        # push path, so callers never invent their own fallback nonce.
        planning = self.job("changes").split(
            "      - name: Plan image rebuild ID\n", 1
        )[1]
        command = re.search(r"^        run: (.+)$", planning, re.MULTILINE).group(1)
        for rebuild_id in ("123-1", "123-2", ""):
            with (
                self.subTest(rebuild_id=rebuild_id),
                tempfile.TemporaryDirectory() as tmp,
            ):
                output = Path(tmp) / "output"
                subprocess.run(
                    ["bash", "-c", command],
                    check=True,
                    env={
                        **os.environ,
                        "GITHUB_OUTPUT": str(output),
                        "PLANNED_REBUILD_ID": rebuild_id,
                    },
                )
                self.assertEqual(output.read_text(), f"id={rebuild_id}\n")


class WorkflowDiskReclamationTest(unittest.TestCase):
    def test_all_image_build_paths_reclaim_hosted_toolcache(self):
        workflow = (
            Path(__file__).resolve().parents[1] / "workflows" / "build-dev-images.yml"
        ).read_text()
        self.assertEqual(workflow.count("sudo rm -rf /opt/hostedtoolcache "), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

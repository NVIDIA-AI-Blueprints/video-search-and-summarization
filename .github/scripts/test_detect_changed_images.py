#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for detect_changed_images.py. Run directly:

    python3 .github/scripts/test_detect_changed_images.py

Builds throwaway git repositories so the push/force-push/initial-push range
semantics are tested against real git, not mocks.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import detect_changed_images as dci  # noqa: E402

INVENTORY = {
    "schema_version": 1,
    "first_party_registry_roots": ["nvcr.io/nvidia/vss-core"],
    "images": [
        {
            "name": "vss-agent",
            "strategy": "build",
            "ghcr_build": True,
            "source_path": "services/agent",
            "context": "services",
            "dockerfile": "services/agent/docker/Dockerfile",
            "platforms": ["linux/amd64", "linux/arm64"],
            "compose_image_names": ["vss-agent"],
        },
        {
            "name": "vss-agent-ui",
            "strategy": "build",
            "ghcr_build": True,
            "source_path": "services/ui",
            "context": ".",
            "dockerfile": "services/ui/Dockerfile",
            "platforms": ["linux/amd64", "linux/arm64"],
            "compose_image_names": ["vss-agent-ui"],
        },
        {
            "name": "vss-alert-ms",
            "strategy": "build",
            "ghcr_build": False,
            "source_path": "services/alert",
            "context": "services/alert",
            "dockerfile": "services/alert/Dockerfile",
            "platforms": ["linux/amd64"],
            "compose_image_names": ["vss-alert-ms"],
        },
        {
            "name": "vss-configurator",
            "strategy": "mirror",
            "platforms": ["linux/amd64"],
            "compose_image_names": ["vss-configurator"],
        },
    ],
}


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def make_repo(tmp: str) -> Path:
    repo = Path(tmp)
    git(repo, "init", "-q", "-b", "develop")
    git(repo, "config", "user.email", "test@test")
    git(repo, "config", "user.name", "test")
    for rel in ("services/agent/app.py", "services/ui/app.js", "docs/readme.md"):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("v1\n")
    (repo / "deploy/docker").mkdir(parents=True)
    (repo / "deploy/docker/container-inventory.json").write_text(json.dumps(INVENTORY))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial")
    return repo


def commit_change(repo: Path, rel: str, content: str, message: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def selected_names(repo: Path, base: str | None) -> list[str]:
    inventory = dci.load_inventory(repo)
    changed = dci.changed_paths(repo, base) if base else None
    entries, _ = dci.select_images(inventory, changed)
    return sorted(entry["name"] for entry in entries)


class ResolveDiffBaseTest(unittest.TestCase):
    def test_develop_push_uses_event_before_not_branch_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            before = git(repo, "rev-parse", "HEAD")
            commit_change(repo, "services/agent/app.py", "v2\n", "agent change")
            base, reason = dci.resolve_diff_base(
                repo, "push", "develop", before, "develop"
            )
            self.assertEqual(base, before)
            self.assertIn("push range", reason)
            self.assertEqual(
                selected_names(repo, base), ["vss-agent", "vss-agent-ui"]
            )

    def test_initial_push_zero_sha_builds_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            base, reason = dci.resolve_diff_base(
                repo, "push", "develop", dci.ZERO_SHA, "develop"
            )
            self.assertIsNone(base)
            self.assertIn("initial push", reason)
            self.assertEqual(selected_names(repo, base), ["vss-agent", "vss-agent-ui"])

    def test_orphaned_before_sha_builds_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            base, reason = dci.resolve_diff_base(
                repo, "push", "develop", "e" * 40, "develop"
            )
            self.assertIsNone(base)
            self.assertIn("unreachable", reason)

    def test_pr_branch_diffs_against_merge_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            fork_point = git(repo, "rev-parse", "HEAD")
            git(repo, "checkout", "-q", "-b", "pull-request/42")
            commit_change(repo, "services/ui/app.js", "v2\n", "ui change 1")
            commit_change(repo, "services/ui/app.js", "v3\n", "ui change 2")
            base, reason = dci.resolve_diff_base(
                repo, "push", "pull-request/42", "ignored", "develop"
            )
            self.assertEqual(base, fork_point)
            self.assertIn("merge-base", reason)
            self.assertEqual(
                selected_names(repo, base), ["vss-agent", "vss-agent-ui"]
            )

    def test_non_push_event_builds_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            base, reason = dci.resolve_diff_base(
                repo, "workflow_dispatch", "develop", "", "develop"
            )
            self.assertIsNone(base)
            self.assertIn("unsupported event", reason)


class SelectImagesTest(unittest.TestCase):
    def test_docs_only_change_builds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            before = git(repo, "rev-parse", "HEAD")
            commit_change(repo, "docs/readme.md", "v2\n", "docs")
            self.assertEqual(selected_names(repo, before), [])

    def test_non_ghcr_build_image_is_never_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            before = git(repo, "rev-parse", "HEAD")
            commit_change(repo, "services/alert/app.py", "v2\n", "alert")
            self.assertEqual(selected_names(repo, before), [])

    def test_build_contract_change_builds_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            before = git(repo, "rev-parse", "HEAD")
            commit_change(
                repo, ".github/workflows/build-dev-images.yml", "on: push\n", "wf"
            )
            self.assertEqual(
                selected_names(repo, before), ["vss-agent", "vss-agent-ui"]
            )

    def test_prefix_is_directory_anchored(self):
        # services/ui-tools must not match the services/ui source path.
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            before = git(repo, "rev-parse", "HEAD")
            commit_change(repo, "services/ui-tools/x.js", "v1\n", "other folder")
            self.assertEqual(selected_names(repo, before), [])

    def test_repository_inventory_builds_both_analytics_images(self):
        repo_root = Path(__file__).resolve().parents[2]
        inventory = dci.load_inventory(repo_root)
        by_name = {entry["name"]: entry for entry in inventory["images"]}

        expected = {
            "vss-video-analytics-api": {
                "context": "services/analytics/video-analytics-api",
                "source_path": "services/analytics/video-analytics-api",
            },
            "vss-behavior-analytics": {
                "context": "services/analytics/behavior-analytics",
                "source_path": "services/analytics/behavior-analytics",
            },
            "vss-agent-gateway": {
                "context": "services/agent-gateway",
                "source_path": "services/agent-gateway",
            },
            "sdr-mw-l": {
                "context": "services/sdrc",
                "source_path": "services/sdrc",
                "native_platform_build": True,
            },
            "vss-configurator": {
                "context": ".",
                "source_path": "services/configurators/vss-configurator",
                "native_platform_build": True,
            },
            "vss-rt-config-adaptor": {
                "context": "services/configurators/vss-rt-config-adaptor",
                "source_path": "services/configurators/vss-rt-config-adaptor",
                "native_platform_build": True,
            },
        }
        for name, expected_fields in expected.items():
            entry = by_name[name]
            self.assertTrue(entry["ghcr_build"])
            self.assertEqual(entry["strategy"], "build")
            self.assertEqual(entry["context"], expected_fields["context"])
            self.assertEqual(entry["source_path"], expected_fields["source_path"])
            if "native_platform_build" in expected_fields:
                self.assertIs(entry["native_platform_build"], True)
            self.assertEqual(entry["platforms"], ["linux/amd64", "linux/arm64"])

        va_entries, _ = dci.select_images(
            inventory, ["services/analytics/video-analytics-api/src/app.ts"]
        )
        self.assertEqual(
            [entry["name"] for entry in va_entries],
            ["vss-video-analytics-api"],
        )

        ba_entries, _ = dci.select_images(
            inventory, ["services/analytics/behavior-analytics/src/app.py"]
        )
        self.assertEqual(
            [entry["name"] for entry in ba_entries],
            ["vss-behavior-analytics"],
        )
        sdr_entries, _ = dci.select_images(
            inventory, ["services/sdrc/app.py"]
        )
        self.assertEqual([entry["name"] for entry in sdr_entries], ["sdr-mw-l"])

        configurator_entries, _ = dci.select_images(
            inventory, ["services/configurators/vss-configurator/app/entrypoint.py"]
        )
        self.assertEqual(
            [entry["name"] for entry in configurator_entries], ["vss-configurator"]
        )

        spatialai_entries, _ = dci.select_images(
            inventory, ["libs/analytics/spatialai-data-utils/release/pyproject.toml"]
        )
        self.assertEqual([entry["name"] for entry in spatialai_entries], [])

        adaptor_entries, _ = dci.select_images(
            inventory, ["services/configurators/vss-rt-config-adaptor/app/config.py"]
        )
        self.assertEqual(
            [entry["name"] for entry in adaptor_entries],
            ["vss-rt-config-adaptor"],
        )

        agent_entries, _ = dci.select_images(
            inventory, ["services/agent/app.py"]
        )
        self.assertEqual(
            [entry["name"] for entry in agent_entries],
            ["vss-agent", "vss-agent-gateway", "vss-agent-ui", "vss-alert-ms"],
        )

        gateway_entries, _ = dci.select_images(
            inventory, ["services/agent-gateway/vss_agent_gateway/server.py"]
        )
        self.assertEqual(
            [entry["name"] for entry in gateway_entries],
            ["vss-agent", "vss-agent-gateway", "vss-agent-ui", "vss-alert-ms"],
        )

    def test_repository_inventory_builds_rtvi_embed_with_lfs_assets(self):
        repo_root = Path(__file__).resolve().parents[2]
        inventory = dci.load_inventory(repo_root)
        entry = next(
            item for item in inventory["images"] if item["name"] == "vss-rt-embed"
        )

        self.assertTrue(entry["ghcr_build"])
        self.assertEqual(entry["strategy"], "build")
        self.assertEqual(entry["source_path"], "services/rtvi/rt-embed")
        self.assertEqual(entry["context"], "services/rtvi/rt-embed")
        self.assertEqual(
            entry["lfs_include"], "services/rtvi/rt-embed/docker/binaries/**"
        )
        self.assertEqual(entry["platforms"], ["linux/amd64", "linux/arm64"])

        sbsa_entry = next(
            item for item in inventory["images"] if item["name"] == "vss-rt-embed-sbsa"
        )
        self.assertTrue(sbsa_entry["ghcr_build"])
        self.assertTrue(sbsa_entry["native_platform_build"])
        self.assertEqual(sbsa_entry["repository"], "vss-rt-embed")
        self.assertEqual(sbsa_entry["tag_suffix"], "-sbsa")
        self.assertEqual(
            sbsa_entry["lfs_include"], "services/rtvi/rt-embed/docker/binaries/**"
        )
        self.assertEqual(sbsa_entry["build_args"], {"ARM_PLATFORM": "sbsa"})
        self.assertEqual(sbsa_entry["platforms"], ["linux/arm64"])
        self.assertEqual(sbsa_entry["compose_image_names"], [])
        self.assertEqual(sbsa_entry["tag_variables"], [])

        entries, _ = dci.select_images(
            inventory, ["services/rtvi/rt-embed/src/main.py"]
        )
        self.assertEqual(
            [item["name"] for item in entries],
            ["vss-rt-embed", "vss-rt-embed-sbsa"],
        )

        matrix = dci.to_matrix(entries)
        self.assertEqual(matrix["include"][1]["build_args"], "ARM_PLATFORM=sbsa")

        matrices = dci.split_build_matrices(entries)
        self.assertIn(
            {
                "name": "vss-rt-embed-sbsa",
                "repository": "vss-rt-embed",
                "tag_suffix": "-sbsa",
                "context": "services/rtvi/rt-embed",
                "dockerfile": "services/rtvi/rt-embed/docker/Dockerfile",
                "lfs_include": "services/rtvi/rt-embed/docker/binaries/**",
                "platforms": "linux/arm64",
                "source_path": "services/rtvi/rt-embed",
                "build_args": "ARM_PLATFORM=sbsa",
                "platform": "linux/arm64",
                "arch": "arm64",
                "runner": "ubuntu-24.04-arm",
                "runner_arch": "ARM64",
                "kernel_arch": "aarch64",
            },
            matrices["native_platform_matrix"]["include"],
        )

    def test_rtvi_embed_lfs_assets_are_verified_in_both_build_paths(self):
        repo_root = Path(__file__).resolve().parents[2]
        workflow = (
            repo_root / ".github/workflows/build-dev-images.yml"
        ).read_text()
        verifier = """      - name: Verify RT Embed LFS shared objects
        if: matrix.name == 'vss-rt-embed' || matrix.name == 'vss-rt-embed-sbsa'
        run: |
          for lfs_asset in \\
            services/rtvi/rt-embed/docker/binaries/igpu/libnvbufsurface.so \\
            services/rtvi/rt-embed/docker/binaries/igpu/libnvbufsurftransform.so \\
            services/rtvi/rt-embed/docker/binaries/igpu/libgstnvdsseimeta.so; do
            test -s \"$lfs_asset\"
          done"""

        self.assertEqual(workflow.count(verifier), 2)

    def test_workflow_passes_dash_prefixed_variant_suffix_unambiguously(self):
        repo_root = Path(__file__).resolve().parents[2]
        workflow = (
            repo_root / ".github/workflows/build-dev-images.yml"
        ).read_text()

        self.assertEqual(
            workflow.count('--tag-suffix="${{ matrix.tag_suffix }}"'), 3
        )
        self.assertNotIn('--tag-suffix "${{ matrix.tag_suffix }}"', workflow)

    def test_native_manifest_uses_declared_platforms(self):
        repo_root = Path(__file__).resolve().parents[2]
        workflow = (
            repo_root / ".github/workflows/build-dev-images.yml"
        ).read_text()

        self.assertIn("PLATFORM_LIST: ${{ matrix.platforms }}", workflow)
        self.assertIn("for platform in ${PLATFORM_LIST//,/ }; do", workflow)
        self.assertIn(
            '"${{ steps.meta.outputs.image }}:${{ steps.meta.outputs.tag }}-${platform##*/}"',
            workflow,
        )

    def test_matrix_shape(self):
        inventory = INVENTORY
        entries, _ = dci.select_images(inventory, ["services/agent/app.py"])
        matrix = dci.to_matrix(entries)
        self.assertEqual(
            matrix,
            {
                "include": [
                    {
                        "name": "vss-agent",
                        "repository": "vss-agent",
                        "tag_suffix": "",
                        "context": "services",
                        "dockerfile": "services/agent/docker/Dockerfile",
                        "lfs_include": "",
                        "platforms": "linux/amd64,linux/arm64",
                        "source_path": "services/agent",
                        "build_args": "",
                    },
                    {
                        "name": "vss-agent-ui",
                        "repository": "vss-agent-ui",
                        "tag_suffix": "",
                        "context": ".",
                        "dockerfile": "services/ui/Dockerfile",
                        "lfs_include": "",
                        "platforms": "linux/amd64,linux/arm64",
                        "source_path": "services/ui",
                        "build_args": "",
                    },
                ]
            },
        )

    def test_matrix_can_target_shared_repository_with_variant_suffix(self):
        entry = {
            "name": "vss-rt-cv-sbsa",
            "repository": "vss-rt-cv",
            "tag_suffix": "-sbsa",
            "context": "services/rtvi/rt-cv",
            "dockerfile": "services/rtvi/rt-cv/docker/Dockerfile.sbsa",
            "platforms": ["linux/arm64"],
            "source_path": "services/rtvi/rt-cv",
        }
        matrix = dci.to_matrix([entry])
        self.assertEqual(
            matrix["include"][0],
            {
                "name": "vss-rt-cv-sbsa",
                "repository": "vss-rt-cv",
                "tag_suffix": "-sbsa",
                "context": "services/rtvi/rt-cv",
                "dockerfile": "services/rtvi/rt-cv/docker/Dockerfile.sbsa",
                "lfs_include": "",
                "platforms": "linux/arm64",
                "source_path": "services/rtvi/rt-cv",
                "build_args": "",
            },
        )

    def test_native_images_use_arch_specific_runners(self):
        entries = [
            {
                "name": "vss-behavior-analytics",
                "context": "services/analytics/behavior-analytics",
                "dockerfile": "services/analytics/behavior-analytics/docker/Dockerfile",
                "platforms": ["linux/amd64", "linux/arm64"],
                "source_path": "services/analytics/behavior-analytics",
            },
            {
                "name": "sdr-mw-l",
                "context": "services/sdrc",
                "dockerfile": "services/sdrc/envoy/Dockerfile.wdm-router",
                "native_platform_build": True,
                "platforms": ["linux/amd64", "linux/arm64"],
                "source_path": "services/sdrc",
            },
            {
                "name": "vss-video-analytics-api",
                "context": "services/analytics/video-analytics-api",
                "dockerfile": (
                    "services/analytics/video-analytics-api/docker/Dockerfile"
                ),
                "platforms": ["linux/amd64", "linux/arm64"],
                "source_path": "services/analytics/video-analytics-api",
            },
            INVENTORY["images"][0],
        ]

        matrices = dci.split_build_matrices(entries)

        self.assertEqual(
            [entry["name"] for entry in matrices["standard_matrix"]["include"]],
            ["vss-video-analytics-api", "vss-agent"],
        )
        self.assertEqual(
            [entry["name"] for entry in matrices["native_matrix"]["include"]],
            ["vss-behavior-analytics", "sdr-mw-l"],
        )
        self.assertEqual(
            [
                (
                    entry["platform"],
                    entry["arch"],
                    entry["runner"],
                    entry["runner_arch"],
                    entry["kernel_arch"],
                )
                for entry in matrices["native_platform_matrix"]["include"]
            ],
            [
                ("linux/amd64", "amd64", "ubuntu-24.04", "X64", "x86_64"),
                ("linux/arm64", "arm64", "ubuntu-24.04-arm", "ARM64", "aarch64"),
                ("linux/amd64", "amd64", "ubuntu-24.04", "X64", "x86_64"),
                ("linux/arm64", "arm64", "ubuntu-24.04-arm", "ARM64", "aarch64"),
            ],
        )

    def test_new_native_images_are_declared_in_the_inventory(self):
        repo_root = Path(__file__).resolve().parents[2]
        inventory = dci.load_inventory(repo_root)
        by_name = {entry["name"]: entry for entry in inventory["images"]}
        self.assertEqual(
            {
                name
                for name, entry in by_name.items()
                if entry.get("native_platform_build") is True
            },
            {
                "sdr-mw-l",
                "vss-configurator",
                "vss-rt-config-adaptor",
                "vss-rt-vlm",
                "vss-rt-vlm-sbsa",
                "vss-vios-sensor",
                "vss-vios-streamprocessing",
                "vss-vios-nvstreamer",
                "vss-vios-ingress",
                "vss-rt-embed",
                "vss-rt-embed-sbsa",
                "vss-rt-vlm-sbsa",
                "vss-video-summarization-sbsa",
            },
        )
        self.assertNotIn(
            "native_platform_build", dci.matrix_entry(by_name["sdr-mw-l"])
        )

    def test_native_matrix_rejects_platform_without_runner(self):
        entries = [
            {
                "name": "vss-behavior-analytics",
                "context": "services/analytics/behavior-analytics",
                "dockerfile": (
                    "services/analytics/behavior-analytics/docker/Dockerfile"
                ),
                "platforms": ["linux/s390x"],
                "source_path": "services/analytics/behavior-analytics",
            }
        ]
        with self.assertRaisesRegex(ValueError, "no native runner configured"):
            dci.split_build_matrices(entries)


class PathsChangedUnderTest(unittest.TestCase):
    def test_descendant_matches(self):
        self.assertTrue(
            dci.paths_changed_under(
                ["libs/analytics/spatialai-data-utils/release/setup.py"],
                "libs/analytics/spatialai-data-utils",
            )
        )

    def test_similarly_named_sibling_does_not_match(self):
        self.assertFalse(
            dci.paths_changed_under(
                ["libs/analytics/spatialai-data-utils-old/setup.py"],
                "libs/analytics/spatialai-data-utils",
            )
        )

    def test_unavailable_diff_fails_open(self):
        self.assertTrue(
            dci.paths_changed_under(
                None,
                "libs/analytics/spatialai-data-utils",
            )
        )


BUILDABLE = [
    {"name": "vss-agent", "source_path": "services/agent",
     "strategy": "build", "ghcr_build": True},
    {"name": "vss-agent-ui", "source_path": "services/ui",
     "strategy": "build", "ghcr_build": True},
]


def _content_repo() -> Path:
    root = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for path in ("services/agent", "services/ui"):
        d = root / path
        d.mkdir(parents=True)
        (d / "f").write_text("x")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"], check=True)
    return root


class ContentTagGapTest(unittest.TestCase):
    """A path diff says the source did not change; it cannot say the content
    tag was ever published. The post-merge retag sources from tree-<sha>, so a
    missing one must pull the image back into the matrix."""

    def test_missing_content_tag_is_added_to_the_matrix(self):
        selected, added = dci.add_missing_content_tags(
            BUILDABLE, [], _content_repo(), "HEAD",
            lambda ref: "vss-agent:" not in ref, "Org")
        self.assertEqual(added, ["vss-agent"])

    def test_present_content_tag_is_not_added(self):
        selected, added = dci.add_missing_content_tags(
            BUILDABLE, [], _content_repo(), "HEAD", lambda _ref: True, "Org")
        self.assertEqual(added, [])
        self.assertEqual(selected, [])

    def test_probe_failure_fails_open_to_building(self):
        """Unknown must never look like 'already published'."""
        _, added = dci.add_missing_content_tags(
            BUILDABLE, [], _content_repo(), "HEAD", lambda _ref: None, "Org")
        self.assertEqual(added, ["vss-agent", "vss-agent-ui"])

    def test_already_selected_images_are_not_reprobed(self):
        selected, added = dci.add_missing_content_tags(
            BUILDABLE, [BUILDABLE[0]], _content_repo(), "HEAD",
            lambda _ref: None, "Org")
        self.assertEqual(added, ["vss-agent-ui"])
        self.assertEqual(len(selected), 2)

    def test_probe_reference_is_the_content_tag(self):
        seen: list[str] = []
        dci.add_missing_content_tags(
            BUILDABLE[:1], [], _content_repo(), "HEAD",
            lambda ref: seen.append(ref) or True, "Org")
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].startswith("ghcr.io/org/vss/vss-agent:tree-"))

    def test_variant_probe_uses_shared_repository_and_suffixed_content_tag(self):
        seen: list[str] = []
        variant = {
            **BUILDABLE[0],
            "name": "vss-agent-sbsa",
            "repository": "vss-agent",
            "tag_suffix": "-sbsa",
        }
        dci.add_missing_content_tags(
            [variant],
            [],
            _content_repo(),
            "HEAD",
            lambda ref: seen.append(ref) or True,
            "Org",
        )
        self.assertEqual(len(seen), 1)
        self.assertRegex(
            seen[0],
            r"^ghcr\.io/org/vss/vss-agent:tree-[0-9a-f]{40}-sbsa$",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

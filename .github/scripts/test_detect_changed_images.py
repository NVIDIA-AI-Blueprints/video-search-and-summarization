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
            "lfs_include": "services/agent/3rdparty/ffmpeg/*",
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
                        "context": "services",
                        "dockerfile": "services/agent/docker/Dockerfile",
                        "lfs_include": "services/agent/3rdparty/ffmpeg/*",
                        "platforms": "linux/amd64,linux/arm64",
                        "source_path": "services/agent",
                    },
                    {
                        "name": "vss-agent-ui",
                        "context": ".",
                        "dockerfile": "services/ui/Dockerfile",
                        "lfs_include": "",
                        "platforms": "linux/amd64,linux/arm64",
                        "source_path": "services/ui",
                    },
                ]
            },
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the intentional Docker/Helm image-channel split."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_MARKER = "HELM_RELEASE_CHANNEL_POLICY"
GHCR_ROOT = "ghcr.io/nvidia-ai-blueprints/vss"
NGC_STAGING_ROOT = "nvcr.io/nvstaging/vss-core"

HELM_VALUES = {
    "vss-agent": [
        "deploy/helm/services/agent/charts/agent/values.yaml",
        "deploy/helm/services/agent/charts/va-mcp/values.yaml",
    ],
    "vss-agent-ui": ["deploy/helm/services/ui/values.yaml"],
    "vss-alert-ms": ["deploy/helm/services/alert/values.yaml"],
}
COMPOSE_FILES = {
    "vss-agent": "deploy/docker/services/agent/compose.yml",
    "vss-agent-ui": "deploy/docker/services/ui/compose.yml",
    "vss-alert-ms": "deploy/docker/services/alert/compose.yml",
}


def image_coordinates(path: Path) -> tuple[str, str]:
    text = path.read_text()
    match = re.search(
        rf"{POLICY_MARKER}:[^\n]*\n"
        r"(?:#[^\n]*\n)*"
        r"image:\s*\n"
        r"\s+repository:\s*(\S+)\s*\n"
        r'\s+tag:\s*"?([^"\s]+)"?',
        text,
    )
    if match is None:
        raise AssertionError(f"{path} lacks a policy-marked image block")
    return match.group(1), match.group(2)


class HelmReleaseChannelPolicyTest(unittest.TestCase):
    def test_policy_covers_every_github_built_image(self):
        inventory = json.loads(
            (REPO_ROOT / "deploy/docker/container-inventory.json").read_text()
        )
        managed = {
            image["name"]
            for image in inventory["images"]
            if image.get("ghcr_build") is True
        }
        self.assertEqual(managed, set(HELM_VALUES))

    def test_helm_uses_explicit_immutable_ngc_staging_pins(self):
        for name, relative_paths in HELM_VALUES.items():
            for relative_path in relative_paths:
                repository, tag = image_coordinates(REPO_ROOT / relative_path)
                self.assertEqual(repository, f"{NGC_STAGING_ROOT}/{name}")
                self.assertNotIn("latest", tag)
                self.assertRegex(tag, r"-[0-9a-f]{8,40}$")

    def test_compose_keeps_the_managed_developer_channel(self):
        for name, relative_path in COMPOSE_FILES.items():
            text = (REPO_ROOT / relative_path).read_text()
            self.assertIn(GHCR_ROOT, text)
            self.assertIn(f"/{name}", text)
            self.assertIn("VSS_CONTAINER_TAG", text)
            self.assertIn("develop-latest", text)

    def test_helm_sync_prompt_forbids_mutable_developer_aliases(self):
        prompt = (REPO_ROOT / ".github/helm-sync/AGENTS.md").read_text()
        self.assertIn(POLICY_MARKER, prompt)
        self.assertIn("Never propose putting `develop-latest` in Helm", prompt)
        self.assertIn("already synced", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)

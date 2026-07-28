#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the shared Docker/Helm managed-image channel."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
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
HELM_HELPERS = {
    "vss-agent": [
        "deploy/helm/services/agent/charts/agent/templates/_helpers.tpl",
        "deploy/helm/services/agent/charts/va-mcp/templates/_helpers.tpl",
    ],
    "vss-agent-ui": ["deploy/helm/services/ui/templates/_helpers.tpl"],
    "vss-alert-ms": ["deploy/helm/services/alert/templates/_helpers.tpl"],
}
COMPOSE_FILES = {
    "vss-agent": "deploy/docker/services/agent/compose.yml",
    "vss-agent-ui": "deploy/docker/services/ui/compose.yml",
    "vss-alert-ms": "deploy/docker/services/alert/compose.yml",
}
# Built and published to GHCR by GitHub, but deliberately still pinned to its
# NGC release coordinate in compose/Helm. Moving it onto the develop-latest
# channel is a separate, intentional decision.
UNMANAGED_GHCR_IMAGES = {"vss-video-summarization"}


def image_coordinates(path: Path) -> tuple[str, str]:
    text = path.read_text()
    match = re.search(
        r"image:\s*\n"
        r"\s+repository:\s*(\S+)\s*\n"
        r'\s+tag:\s*"?([^"\s]+)"?',
        text,
    )
    if match is None:
        raise AssertionError(f"{path} lacks an image block")
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
        } - UNMANAGED_GHCR_IMAGES
        self.assertEqual(managed, set(HELM_VALUES))

    def test_helm_defaults_to_managed_ghcr_channel(self):
        for name, relative_paths in HELM_VALUES.items():
            for relative_path in relative_paths:
                repository, tag = image_coordinates(REPO_ROOT / relative_path)
                self.assertEqual(repository, f"{GHCR_ROOT}/{name}")
                self.assertEqual(tag, "develop-latest")

    def test_helm_supports_one_prefix_and_tag_override(self):
        for name, relative_paths in HELM_HELPERS.items():
            for relative_path in relative_paths:
                text = (REPO_ROOT / relative_path).read_text()
                self.assertIn('"container_prefix"', text)
                self.assertIn('"container_tag"', text)
                self.assertIn(f'"%s/{name}"', text)
                self.assertIn("trimSuffix", text)

    def test_search_profile_does_not_pin_managed_ui_image(self):
        text = (
            REPO_ROOT
            / "deploy/helm/developer-profiles/dev-profile-search/values.yaml"
        ).read_text()
        self.assertNotIn(f"{NGC_STAGING_ROOT}/vss-agent-ui", text)

    def test_compose_keeps_the_managed_developer_channel(self):
        for name, relative_path in COMPOSE_FILES.items():
            text = (REPO_ROOT / relative_path).read_text()
            self.assertIn(GHCR_ROOT, text)
            self.assertIn(f"/{name}", text)
            self.assertIn("VSS_CONTAINER_TAG", text)
            self.assertIn("develop-latest", text)

    def test_helm_sync_prompt_enforces_shared_channel(self):
        prompt = (REPO_ROOT / ".github/helm-sync/AGENTS.md").read_text()
        self.assertIn("Shared managed-image channel", prompt)
        self.assertIn("global.container_prefix", prompt)
        self.assertIn("global.container_tag", prompt)
        self.assertIn(GHCR_ROOT, prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)

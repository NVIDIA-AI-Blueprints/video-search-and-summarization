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
    "vss-video-analytics-api": [
        "deploy/helm/services/analytics/charts/video-analytics-api/values.yaml",
    ],
    "vss-behavior-analytics": [
        "deploy/helm/services/analytics/charts/behavior-analytics/values.yaml",
    ],
    "vss-video-summarization": [
        "deploy/helm/services/video-summarization/values.yaml",
    ],
    "vss-rt-cv": [
        "deploy/helm/services/rtvi/charts/rtvi-cv/values.yaml",
    ],
    "vss-vios-sensor": [
        "deploy/helm/services/vios/charts/vios-sensor/values.yaml",
    ],
    "vss-vios-streamprocessing": [
        "deploy/helm/services/vios/charts/vios-streamprocessing/values.yaml",
    ],
    "vss-vios-nvstreamer": [
        "deploy/helm/services/vios/charts/vios-nvstreamer/values.yaml",
    ],
    "vss-vios-ingress": [
        "deploy/helm/services/vios/charts/vios-ingress/values.yaml",
    ],
    "sdr-mw-l": ["deploy/helm/services/infra/charts/sdrc/values.yaml"],
    "vss-configurator": [
        "deploy/helm/services/bp-configurator/values.yaml",
        "deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app/values.yaml",
        "deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app/values.yaml",
        "deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app/values.yaml",
    ],
    "vss-rt-config-adaptor": [
        "deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app/values.yaml",
    ],
    "vss-rt-cv-mv3dt-bev-fusion": [
        "deploy/helm/services/rtvi/charts/rtvi-cv/values.yaml",
    ],
    "vss-rt-cv-mv3dt-config-init": [
        "deploy/helm/services/rtvi/charts/rtvi-cv/values.yaml",
    ],
    "vss-rt-embed": [
        "deploy/helm/services/rtvi/charts/rtvi-embed/values.yaml",
        "deploy/helm/services/rtvi/charts/rtvi-embed/overrides_rtvi_embed.yaml",
        "deploy/helm/developer-profiles/dev-profile-search/values.yaml",
    ],
    "vss-rt-vlm": [
        "deploy/helm/services/rtvi/charts/rtvi-vlm/values.yaml",
    ],
}
HELM_HELPERS = {
    "vss-agent": [
        "deploy/helm/services/agent/charts/agent/templates/_helpers.tpl",
        "deploy/helm/services/agent/charts/va-mcp/templates/_helpers.tpl",
    ],
    "vss-agent-ui": ["deploy/helm/services/ui/templates/_helpers.tpl"],
    "vss-alert-ms": ["deploy/helm/services/alert/templates/_helpers.tpl"],
    "vss-video-analytics-api": [
        "deploy/helm/services/analytics/charts/video-analytics-api/templates/_helpers.tpl",
    ],
    "vss-behavior-analytics": [
        "deploy/helm/services/analytics/charts/behavior-analytics/templates/_helpers.tpl",
    ],
    "vss-video-summarization": [
        "deploy/helm/services/video-summarization/templates/_helpers.tpl",
    ],
    # The chart is named rtvi-cv and its helpers are "vss-rtvi-cv.*", but the
    # managed image is vss-rt-cv -- so the printf target below is the image
    # name, not the helper prefix.
    "vss-rt-cv": [
        "deploy/helm/services/rtvi/charts/rtvi-cv/templates/_helpers.tpl",
    ],
    "vss-vios-sensor": [
        "deploy/helm/services/vios/charts/vios-sensor/templates/_helpers.tpl",
    ],
    "vss-vios-streamprocessing": [
        "deploy/helm/services/vios/charts/vios-streamprocessing/templates/_helpers.tpl",
    ],
    "vss-vios-nvstreamer": [
        "deploy/helm/services/vios/charts/vios-nvstreamer/templates/_helpers.tpl",
    ],
    "vss-vios-ingress": [
        "deploy/helm/services/vios/charts/vios-ingress/templates/_helpers.tpl",
    ],
    "sdr-mw-l": ["deploy/helm/services/infra/charts/sdrc/templates/_helpers.tpl"],
    "vss-configurator": [
        "deploy/helm/services/bp-configurator/templates/_helpers.tpl",
    ],
    "vss-rt-config-adaptor": [
        "deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app/templates/warehouse-extra-services.yaml",
        "deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app/templates/warehouse-extra-services.yaml",
    ],
    "vss-rt-cv-mv3dt-bev-fusion": [
        "deploy/helm/services/rtvi/charts/rtvi-cv/templates/_helpers.tpl",
    ],
    "vss-rt-cv-mv3dt-config-init": [
        "deploy/helm/services/rtvi/charts/rtvi-cv/templates/_helpers.tpl",
    ],
    "vss-rt-embed": [
        "deploy/helm/services/rtvi/charts/rtvi-embed/templates/_helpers.tpl",
    ],
    "vss-rt-vlm": [
        "deploy/helm/services/rtvi/charts/rtvi-vlm/templates/_helpers.tpl",
    ],
}
COMPOSE_FILES = {
    "vss-agent": ["deploy/docker/services/agent/compose.yml"],
    "vss-agent-ui": ["deploy/docker/services/ui/compose.yml"],
    "vss-alert-ms": ["deploy/docker/services/alert/compose.yml"],
    "vss-video-summarization": [
        "deploy/docker/services/video-summarization/compose.yml"
    ],
    "sdr-mw-l": ["deploy/docker/services/infra/sdrc/docker-compose.yaml"],
    "vss-configurator": [
        "deploy/docker/services/configurators/vss-configurator/docker-compose.yaml",
    ],
    "vss-rt-config-adaptor": [
        "deploy/docker/industry-profiles/warehouse-operations/warehouse-3d-app/warehouse-3d-app.yml",
    ],
    "vss-rt-vlm": [
        "deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml",
    ],
}
COMPOSE_TAG_VARIABLES = {
    "vss-rt-vlm": "VSS_RT_VLM_TAG",
    "vss-video-summarization": "VSS_VIDEO_SUMMARIZATION_TAG",
}


def image_coordinates(path: Path, image_name: str) -> tuple[str, str]:
    text = path.read_text()
    matches = re.finditer(
        r"image:\s*\n"
        r"\s+repository:\s*(\S+)\s*\n"
        r'\s+tag:\s*"?([^"\s]+)"?',
        text,
    )
    for match in matches:
        repository = match.group(1)
        if repository == f"{GHCR_ROOT}/{image_name}" or repository.endswith(
            f"/{image_name}"
        ):
            return repository, match.group(2)
    raise AssertionError(f"{path} lacks an image block for {image_name}")


class HelmReleaseChannelPolicyTest(unittest.TestCase):
    def test_policy_covers_every_github_built_image(self):
        inventory = json.loads(
            (REPO_ROOT / "deploy/docker/container-inventory.json").read_text()
        )
        # Tagged variants (tag_suffix, e.g. -sbsa) share their base image's GHCR
        # repository and are selected by overriding the tag on that same Helm
        # image block, so they have no image block of their own for this policy
        # to cover. Listing one here could not pass either: the check below
        # asserts repository == "<root>/<name>", and a variant's repository is
        # deliberately the base name. They are excluded here by the same signal
        # release_set.py already uses for Compose references: a variant carries
        # no ``compose_image_names`` of its own.
        managed = {
            image["name"]
            for image in inventory["images"]
            if image.get("ghcr_build") is True and image.get("compose_image_names")
        }
        self.assertEqual(managed, set(HELM_VALUES))

    def test_tagged_variants_share_a_managed_repository_without_defaults(self):
        inventory = json.loads(
            (REPO_ROOT / "deploy/docker/container-inventory.json").read_text()
        )
        variants = [
            image
            for image in inventory["images"]
            if image.get("ghcr_build") is True and image.get("tag_suffix")
        ]
        self.assertTrue(variants)
        for variant in variants:
            self.assertEqual(variant.get("compose_image_names"), [])
            self.assertEqual(variant.get("tag_variables"), [])
            repository = variant.get("repository")
            self.assertTrue(repository)
            self.assertIn(repository, HELM_VALUES)

    def test_helm_defaults_to_managed_ghcr_channel(self):
        for name, relative_paths in HELM_VALUES.items():
            for relative_path in relative_paths:
                repository, tag = image_coordinates(REPO_ROOT / relative_path, name)
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
        for name, relative_paths in COMPOSE_FILES.items():
            for relative_path in relative_paths:
                text = (REPO_ROOT / relative_path).read_text()
                self.assertIn(GHCR_ROOT, text)
                self.assertIn(f"/{name}", text)
                self.assertIn(COMPOSE_TAG_VARIABLES.get(name, "VSS_CONTAINER_TAG"), text)
                self.assertIn("develop-latest", text)

    def test_helm_sync_prompt_enforces_shared_channel(self):
        prompt = (REPO_ROOT / ".github/helm-sync/AGENTS.md").read_text()
        self.assertIn("Shared managed-image channel", prompt)
        self.assertIn("global.container_prefix", prompt)
        self.assertIn("global.container_tag", prompt)
        self.assertIn(GHCR_ROOT, prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)

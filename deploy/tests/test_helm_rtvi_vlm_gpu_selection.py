# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Renaming the GPU resource and leaving device selection to the plugin are separate."""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from functools import cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CHART = REPO_ROOT / "helm" / "services" / "rtvi" / "charts" / "rtvi-vlm"

REQUIRED = (
    "enabled=true",
    "global.ngcApiSecret.name=ngc-secret",
    "global.ngcApiSecret.key=NGC_API_KEY",
)

helm_required = unittest.skipUnless(
    shutil.which("helm"), "helm is not installed; chart rendering cannot be checked"
)


@cache
def _container(set_values: tuple[str, ...] = ()) -> dict:
    env = os.environ.copy()
    env["HELM_REPOSITORY_CONFIG"] = os.devnull
    command = ["helm", "template", "test", str(CHART)]
    for value in REQUIRED + set_values:
        command.extend(["--set", value])
    result = subprocess.run(
        command, cwd=CHART, env=env, capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    deployments = [
        document
        for document in yaml.safe_load_all(result.stdout)
        if document and document.get("kind") == "Deployment"
    ]
    if len(deployments) != 1:
        raise AssertionError(f"expected one Deployment, found {len(deployments)}")
    return deployments[0]["spec"]["template"]["spec"]["containers"][0]


def _gpu_limits(container: dict) -> dict:
    limits = (container.get("resources") or {}).get("limits") or {}
    return {k: v for k, v in limits.items() if "gpu" in k.lower() or "mig" in k.lower()}


def _visible_devices(container: dict):
    for entry in container.get("env") or []:
        if entry.get("name") == "NVIDIA_VISIBLE_DEVICES":
            return entry.get("value")
    return None


class GpuSelectionTests(unittest.TestCase):
    """Three renderings, so a later template change cannot recouple them."""

    @helm_required
    def test_defaults_keep_the_counted_gpu_and_set_visible_devices(self):
        container = _container()

        self.assertEqual(_gpu_limits(container), {"nvidia.com/gpu": 1})
        self.assertIsNotNone(_visible_devices(container))

    @helm_required
    def test_a_renamed_resource_still_implies_the_plugin_selects(self):
        """The documented MIG case, unchanged: renaming also drops the variable."""
        container = _container(("gpuResourceName=nvidia.com/mig-3g.40gb",))

        self.assertEqual(_gpu_limits(container), {"nvidia.com/mig-3g.40gb": 1})
        self.assertIsNone(_visible_devices(container))

    @helm_required
    def test_the_plugin_can_select_without_renaming_the_resource(self):
        """The case that had no expression before: a cluster whose plugin assigns
        devices by UUID while still serving a plain nvidia.com/gpu."""
        container = _container(("pluginSelectsDevices=true",))

        self.assertEqual(_gpu_limits(container), {"nvidia.com/gpu": 1})
        self.assertIsNone(_visible_devices(container))

    @helm_required
    def test_the_two_settings_are_independent(self):
        """Asking for both is the same as asking for each: no interaction."""
        both = _container(
            ("gpuResourceName=nvidia.com/mig-3g.40gb", "pluginSelectsDevices=true")
        )

        self.assertEqual(_gpu_limits(both), {"nvidia.com/mig-3g.40gb": 1})
        self.assertIsNone(_visible_devices(both))


if __name__ == "__main__":
    unittest.main()

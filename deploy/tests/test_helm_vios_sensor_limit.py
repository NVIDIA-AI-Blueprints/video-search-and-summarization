# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render the VIOS sensor admission limit through the real Helm templates."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

HELM_ROOT = Path(__file__).resolve().parents[1] / "helm"
COMPONENTS = ("vios-sensor", "vios-streamprocessing")
PROFILES = ("dev-profile-base", "dev-profile-lvs")


def render(chart: Path, values: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["helm", "template", "test", str(chart), "-f", "-"],
        input=yaml.safe_dump(values),
        text=True,
        capture_output=True,
        check=False,
    )


def documents(result: subprocess.CompletedProcess) -> list[dict]:
    if result.returncode:
        raise AssertionError(result.stderr)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def config(docs: list[dict], component: str) -> dict:
    return next(
        doc
        for doc in docs
        if doc["kind"] == "ConfigMap"
        and doc["metadata"]["labels"].get("app.kubernetes.io/name") == f"vss-{component}"
    )["data"]


@unittest.skipUnless(shutil.which("helm"), "helm is required for chart rendering")
class ViosSensorLimitTests(unittest.TestCase):
    def test_default_and_integer_overrides(self):
        for component in COMPONENTS:
            chart = HELM_ROOT / "services/vios/charts" / component
            for values, expected in (
                ({}, 100),
                ({"maxSensorsSupported": 1}, 1),
                ({"maxSensorsSupported": 500}, 500),
                ({"maxSensorsSupported": 2147483647}, 2147483647),
            ):
                with self.subTest(component=component, values=values):
                    docs = documents(render(chart, {"enabled": True, **values}))
                    actual = json.loads(config(docs, component)["vst_config.json"])
                    self.assertIs(type(actual["onvif"]["max_devices_supported"]), int)
                    self.assertEqual(actual["onvif"]["max_devices_supported"], expected)

    def test_invalid_limits_fail_before_deployment(self):
        for component in COMPONENTS:
            chart = HELM_ROOT / "services/vios/charts" / component
            for value in (None, 0, -1, True, 1.5, "500", "abc", 2147483648):
                with self.subTest(component=component, value=value):
                    result = render(chart, {"enabled": True, "maxSensorsSupported": value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("maxSensorsSupported", result.stderr)

    def test_only_limit_and_rollout_checksum_change(self):
        for component in COMPONENTS:
            chart = HELM_ROOT / "services/vios/charts" / component
            with self.subTest(component=component):
                before = documents(render(chart, {"enabled": True}))
                after = documents(render(chart, {"enabled": True, "maxSensorsSupported": 500}))
                after_config = config(after, component)
                updated = json.loads(after_config["vst_config.json"])
                updated["onvif"]["max_devices_supported"] = 100
                self.assertEqual(updated, json.loads(config(before, component)["vst_config.json"]))
                after_config["vst_config.json"] = config(before, component)["vst_config.json"]
                workload_kind = "Deployment" if component == "vios-sensor" else "StatefulSet"
                workloads = [d for d in before if d["kind"] in ("Deployment", "StatefulSet")]
                self.assertEqual([d["kind"] for d in workloads], [workload_kind])
                self.assertEqual(len(before), len(after))
                for old, new in zip(before, after):
                    if old["kind"] == workload_kind:
                        old_annotations = old["spec"]["template"]["metadata"]["annotations"]
                        new_annotations = new["spec"]["template"]["metadata"]["annotations"]
                        self.assertNotEqual(
                            old_annotations["checksum/config"], new_annotations["checksum/config"]
                        )
                        new_annotations["checksum/config"] = old_annotations["checksum/config"]
                self.assertEqual(before, after)

    def test_developer_profile_values_reach_both_services(self):
        for profile in PROFILES:
            chart = HELM_ROOT / "developer-profiles" / profile
            if not (chart / "charts").is_dir():
                subprocess.run(
                    ["helm", "dependency", "build", str(chart)], check=True, capture_output=True
                )
            with self.subTest(profile=profile):
                values = {
                    "ngc": {"createSecrets": False},
                    "vios": {
                        f"vss-{component}": {"maxSensorsSupported": 500} for component in COMPONENTS
                    },
                }
                docs = documents(render(chart, values))
                for component in COMPONENTS:
                    actual = json.loads(config(docs, component)["vst_config.json"])
                    self.assertEqual(actual["onvif"]["max_devices_supported"], 500)


if __name__ == "__main__":
    unittest.main()

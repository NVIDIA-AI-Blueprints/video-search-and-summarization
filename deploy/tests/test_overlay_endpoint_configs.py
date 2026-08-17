# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for VIOS overlay HTTP endpoint deploy config wiring.

Docker: endpoints present but empty (bind-mounts remain the default).
Helm: placeholders + values substitute absolute URLs; writable configs when set.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKER_ROOT = REPO_ROOT / "docker"
HELM_SP = REPO_ROOT / "helm" / "services" / "vios" / "charts" / "vios-streamprocessing"
WAREHOUSE_HELM = REPO_ROOT / "helm" / "industry-profiles" / "warehouse-operations"

CALIB_KEY = "calibration_file_endpoint"
FLOOR_KEY = "floormap_image_endpoint"
CALIB_PLACEHOLDER = "__CALIBRATION_FILE_ENDPOINT__"
FLOOR_PLACEHOLDER = "__FLOORMAP_IMAGE_ENDPOINT__"

API_CALIB = "http://vss-video-analytics-api:8081/config/calibration"
FLOOR_2D = (
    "http://vss-video-analytics-api:8081/config/calibration/image"
    "?place=building%3DWarehouse%2Froom%3DRoom-1&view=plan-view"
)
FLOOR_3D = (
    "http://vss-video-analytics-api:8081/config/calibration/image"
    "?place=building%3DWarehouse%2Froom%3DRoom-1%2Fregion%3DRegion-1&view=plan-view"
)


def _overlay(path: Path) -> dict:
    data = json.loads(path.read_text())
    assert "overlay" in data, f"missing overlay in {path}"
    return data["overlay"]


def _helm_template(extra_values: dict | None = None) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            "helm",
            "template",
            "vss",
            str(HELM_SP),
            "--set",
            "enabled=true",
            "--set",
            "useStatefulSet=true",
        ]
        if extra_values:
            values_path = Path(tmp) / "values.yaml"
            values_path.write_text(yaml.safe_dump(extra_values))
            cmd.extend(["-f", str(values_path)])
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return result.stdout


class DockerOverlayEndpointConfigTests(unittest.TestCase):
    """Docker defaults keep empty endpoints; bind-mounts stay for 3d/mv3dt."""

    def test_base_vst_config_has_empty_endpoints(self):
        overlay = _overlay(DOCKER_ROOT / "services" / "vios" / "configs" / "vst_config.json")
        self.assertIn(CALIB_KEY, overlay)
        self.assertIn(FLOOR_KEY, overlay)
        self.assertEqual(overlay[CALIB_KEY], "")
        self.assertEqual(overlay[FLOOR_KEY], "")

    def test_warehouse_profile_vst_configs_have_empty_endpoints(self):
        profiles = [
            "warehouse-2d-app",
            "warehouse-3d-app",
            "warehouse-mv3dt-app",
        ]
        for name in profiles:
            path = (
                DOCKER_ROOT
                / "industry-profiles"
                / "warehouse-operations"
                / name
                / "vst"
                / "configs"
                / "vst_config.json"
            )
            with self.subTest(profile=name):
                overlay = _overlay(path)
                self.assertEqual(overlay[CALIB_KEY], "")
                self.assertEqual(overlay[FLOOR_KEY], "")

    def test_streamprocessing_compose_keeps_calibration_bind_mounts(self):
        compose_path = (
            DOCKER_ROOT
            / "services"
            / "vios"
            / "streamprocessing"
            / "docker-compose.yaml"
        )
        text = compose_path.read_text()
        # 3d + mv3dt sample-data bind-mounts must remain (not replaced by endpoints).
        self.assertIn(
            "warehouse-3d-app/calibration/sample-data/${SAMPLE_VIDEO_DATASET}/calibration.json"
            ":/home/vst/vst_release/configs/calibration.json",
            text,
        )
        self.assertIn(
            "warehouse-3d-app/calibration/sample-data/${SAMPLE_VIDEO_DATASET}/images/Top.png"
            ":/home/vst/vst_release/configs/Top.png",
            text,
        )
        self.assertIn(
            "warehouse-mv3dt-app/calibration/sample-data/${SAMPLE_VIDEO_DATASET}/calibration.json"
            ":/home/vst/vst_release/configs/calibration.json",
            text,
        )
        self.assertIn(
            "warehouse-mv3dt-app/calibration/sample-data/${SAMPLE_VIDEO_DATASET}/images/Top.png"
            ":/home/vst/vst_release/configs/Top.png",
            text,
        )


class HelmOverlayEndpointConfigTests(unittest.TestCase):
    """Helm chart substitutes endpoints and seeds writable configs when set."""

    def test_chart_vst_config_uses_placeholders(self):
        overlay = _overlay(HELM_SP / "configs" / "vst_config.json")
        self.assertEqual(overlay[CALIB_KEY], CALIB_PLACEHOLDER)
        self.assertEqual(overlay[FLOOR_KEY], FLOOR_PLACEHOLDER)

    def test_default_values_endpoints_empty(self):
        values = yaml.safe_load((HELM_SP / "values.yaml").read_text())
        self.assertEqual(values.get("calibrationFileEndpoint"), "")
        self.assertEqual(values.get("floormapImageEndpoint"), "")
        self.assertIn("configSeed", values)

    def test_warehouse_profile_values_set_endpoints(self):
        expected = {
            "warehouse-2d-app": (API_CALIB, FLOOR_2D),
            "warehouse-3d-app": (API_CALIB, FLOOR_3D),
            "warehouse-mv3dt-app": (API_CALIB, FLOOR_3D),
        }
        for name, (calib, floor) in expected.items():
            path = WAREHOUSE_HELM / name / "values.yaml"
            values = yaml.safe_load(path.read_text())
            sp = values["vios"]["vss-vios-streamprocessing"]
            with self.subTest(profile=name):
                self.assertEqual(sp["calibrationFileEndpoint"], calib)
                self.assertEqual(sp["floormapImageEndpoint"], floor)

    def test_helm_template_empty_endpoints_readonly_configmap(self):
        rendered = _helm_template()
        self.assertNotIn("seed-vst-configs", rendered)
        self.assertIn("readOnly: true", rendered)
        # ConfigMap data should contain empty endpoint strings, not placeholders.
        self.assertNotIn(CALIB_PLACEHOLDER, rendered)
        self.assertNotIn(FLOOR_PLACEHOLDER, rendered)
        self.assertRegex(rendered, rf'"{CALIB_KEY}"\s*:\s*""')
        self.assertRegex(rendered, rf'"{FLOOR_KEY}"\s*:\s*""')

    def test_helm_template_endpoints_enable_writable_seed(self):
        rendered = _helm_template(
            {
                "calibrationFileEndpoint": API_CALIB,
                "floormapImageEndpoint": FLOOR_3D,
            }
        )
        self.assertIn("seed-vst-configs", rendered)
        self.assertIn("configs-cm", rendered)
        self.assertIn("emptyDir: {}", rendered)
        self.assertIn(API_CALIB, rendered)
        self.assertIn(FLOOR_3D, rendered)
        self.assertNotIn(CALIB_PLACEHOLDER, rendered)
        self.assertNotIn(FLOOR_PLACEHOLDER, rendered)
        # Writable path: configs mount should not force readOnly when endpoints set.
        # Find the streamprocessing container configs mount block.
        self.assertIn("name: seed-vst-configs", rendered)


if __name__ == "__main__":
    unittest.main()

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""max_devices_supported is a Helm value in the three VIOS subcharts.

VST refuses its 101st sensor because the limit was a literal 100 in each
subchart's vst_config.json. global.vios.maxDevicesSupported now sets it for
sensor, streamprocessing and nvstreamer, and a per-subchart maxDevicesSupported
overrides it. With neither set the rendered file still says 100.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

VIOS = Path(__file__).resolve().parents[1] / "helm" / "services" / "vios"
SUBCHARTS = ("vss-vios-sensor", "vss-vios-streamprocessing", "vss-vios-nvstreamer")

helm_required = unittest.skipUnless(
    shutil.which("helm"), "helm is not installed; chart rendering cannot be checked"
)


def _limits(*overrides: str) -> dict[str, int]:
    """Render the umbrella chart and return max_devices_supported per subchart."""
    command = ["helm", "template", "t", str(VIOS), "--set", "vss-vios-nvstreamer.enabled=true"]
    for value in overrides:
        command += ["--set", value]
    out = subprocess.run(command, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    limits = {}
    for doc in yaml.safe_load_all(out.stdout):
        if not doc or doc.get("kind") != "ConfigMap":
            continue
        raw = (doc.get("data") or {}).get("vst_config.json")
        if raw is None:
            continue
        name = next(s for s in SUBCHARTS if s in doc["metadata"]["labels"]["app.kubernetes.io/name"])
        limits[name] = _find(json.loads(raw))
    assert set(limits) == set(SUBCHARTS), limits
    return limits


def _find(node):
    if isinstance(node, dict):
        if "max_devices_supported" in node:
            return node["max_devices_supported"]
        for child in node.values():
            found = _find(child)
            if found is not None:
                return found
    return None


@helm_required
class MaxDevicesSupported(unittest.TestCase):
    def test_the_default_is_100_in_all_three(self):
        self.assertEqual(_limits(), dict.fromkeys(SUBCHARTS, 100))

    def test_the_global_value_sets_all_three(self):
        self.assertEqual(_limits("global.vios.maxDevicesSupported=1000"),
                         dict.fromkeys(SUBCHARTS, 1000))

    def test_a_subchart_value_overrides_the_global_one(self):
        self.assertEqual(
            _limits("global.vios.maxDevicesSupported=1000",
                    "vss-vios-sensor.maxDevicesSupported=50"),
            {"vss-vios-sensor": 50, "vss-vios-streamprocessing": 1000, "vss-vios-nvstreamer": 1000},
        )

    def test_a_subchart_value_alone_leaves_the_others_at_100(self):
        self.assertEqual(
            _limits("vss-vios-nvstreamer.maxDevicesSupported=7"),
            {"vss-vios-sensor": 100, "vss-vios-streamprocessing": 100, "vss-vios-nvstreamer": 7},
        )


if __name__ == "__main__":
    unittest.main()

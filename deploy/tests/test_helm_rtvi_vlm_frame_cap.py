# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""RT-VLM frame-cap and context overrides survive profiles' complete env lists."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from functools import cache
from pathlib import Path

import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm"
FRAME_CAP = "RTVI_VLLM_MM_PROCESSOR_VIDEO_NUM_FRAMES"
CONTEXT_LENGTH = "VLM_MAX_MODEL_LEN"
TARGETS = ("rtvi-vlm", "dev-profile-base", "dev-profile-lvs")

helm_required = unittest.skipUnless(
    shutil.which("helm"), "helm is not installed; chart rendering cannot be checked"
)


def _chart(chart: str) -> Path:
    if chart == "rtvi-vlm":
        return HELM_DIR / "services" / "rtvi" / "charts" / chart
    return HELM_DIR / "developer-profiles" / chart


@cache
def _dependencies(chart: str) -> None:
    if chart == "rtvi-vlm":
        return
    result = subprocess.run(
        ["helm", "dependency", "build", str(_chart(chart))],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)


@cache
def _env(chart: str, override: str | None = None, context_length: str | None = None) -> list[dict]:
    _dependencies(chart)
    command = ["helm", "template", "frame-cap-test", str(_chart(chart))]
    if chart == "rtvi-vlm":
        command += [
            "--set",
            "enabled=true",
            "--set",
            "global.ngcApiSecret.name=ngc-secret",
            "--set",
            "global.ngcApiSecret.key=NGC_API_KEY",
        ]
    prefix = "" if chart == "rtvi-vlm" else "rtvi.vss-rtvi-vlm."
    for name, value in ((FRAME_CAP, override), (CONTEXT_LENGTH, context_length)):
        if value is not None:
            command += ["--set-string", f"{prefix}envOverrides.{name}={value}"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr)
    containers = [
        container
        for document in yaml.safe_load_all(result.stdout)
        if document and document.get("kind") == "Deployment"
        for container in document["spec"]["template"]["spec"]["containers"]
        if container.get("name") == "vss-rtvi-vlm"
    ]
    if len(containers) != 1:
        raise AssertionError(f"expected one RT-VLM container, found {len(containers)}")
    return containers[0]["env"]


class FrameCapDeclarations(unittest.TestCase):
    def test_every_values_list_declares_the_existing_default_once(self):
        for chart in TARGETS:
            with self.subTest(chart=chart):
                values = yaml.safe_load((_chart(chart) / "values.yaml").read_text())
                if chart != "rtvi-vlm":
                    values = values["rtvi"]["vss-rtvi-vlm"]
                entries = [entry for entry in values["env"] if entry["name"] == FRAME_CAP]
                self.assertEqual(entries, [{"name": FRAME_CAP, "value": "256"}])


@helm_required
class FrameCapRendering(unittest.TestCase):
    def test_default_renders_256_once_in_every_chart(self):
        for chart in TARGETS:
            with self.subTest(chart=chart):
                entries = [entry for entry in _env(chart) if entry["name"] == FRAME_CAP]
                self.assertEqual(entries, [{"name": FRAME_CAP, "value": "256"}])

    def test_override_renders_8192_once_in_every_chart(self):
        for chart in TARGETS:
            with self.subTest(chart=chart):
                entries = [entry for entry in _env(chart, "8192") if entry["name"] == FRAME_CAP]
                self.assertEqual(entries, [{"name": FRAME_CAP, "value": "8192"}])

    def test_override_preserves_every_other_environment_entry(self):
        for chart in TARGETS:
            with self.subTest(chart=chart):
                default = [entry for entry in _env(chart) if entry["name"] != FRAME_CAP]
                overridden = [entry for entry in _env(chart, "8192") if entry["name"] != FRAME_CAP]
                self.assertEqual(overridden, default)


class ContextLengthDeclarations(unittest.TestCase):
    def test_every_values_list_declares_the_existing_default_once(self):
        for chart in TARGETS:
            with self.subTest(chart=chart):
                values = yaml.safe_load((_chart(chart) / "values.yaml").read_text())
                if chart != "rtvi-vlm":
                    values = values["rtvi"]["vss-rtvi-vlm"]
                entries = [entry for entry in values["env"] if entry["name"] == CONTEXT_LENGTH]
                self.assertEqual(entries, [{"name": CONTEXT_LENGTH, "value": "32768"}])


@helm_required
class ContextLengthRendering(unittest.TestCase):
    def test_default_renders_32768_once_in_every_chart(self):
        for chart in TARGETS:
            with self.subTest(chart=chart):
                entries = [entry for entry in _env(chart) if entry["name"] == CONTEXT_LENGTH]
                self.assertEqual(entries, [{"name": CONTEXT_LENGTH, "value": "32768"}])

    def test_override_renders_262144_once_in_every_chart(self):
        for chart in TARGETS:
            with self.subTest(chart=chart):
                entries = [
                    entry
                    for entry in _env(chart, context_length="262144")
                    if entry["name"] == CONTEXT_LENGTH
                ]
                self.assertEqual(entries, [{"name": CONTEXT_LENGTH, "value": "262144"}])

    def test_override_preserves_every_other_environment_entry(self):
        for chart in TARGETS:
            with self.subTest(chart=chart):
                default = [entry for entry in _env(chart) if entry["name"] != CONTEXT_LENGTH]
                overridden = [
                    entry
                    for entry in _env(chart, context_length="262144")
                    if entry["name"] != CONTEXT_LENGTH
                ]
                self.assertEqual(overridden, default)

    def test_context_and_frame_cap_can_be_overridden_together(self):
        for chart in TARGETS:
            with self.subTest(chart=chart):
                overridden = _env(chart, "8192", "262144")
                for name, value in ((FRAME_CAP, "8192"), (CONTEXT_LENGTH, "262144")):
                    entries = [entry for entry in overridden if entry["name"] == name]
                    self.assertEqual(entries, [{"name": name, "value": value}])
                unchanged = [
                    entry
                    for entry in _env(chart)
                    if entry["name"] not in {FRAME_CAP, CONTEXT_LENGTH}
                ]
                self.assertEqual(
                    [
                        entry
                        for entry in overridden
                        if entry["name"] not in {FRAME_CAP, CONTEXT_LENGTH}
                    ],
                    unchanged,
                )


if __name__ == "__main__":
    unittest.main()

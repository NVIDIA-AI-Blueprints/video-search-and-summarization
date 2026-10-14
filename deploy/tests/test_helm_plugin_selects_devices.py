# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""pluginSelectsDevices drops NVIDIA_VISIBLE_DEVICES, and nothing else does.

The variable names GPUs from inside the container, which overrides whatever
assigned them from outside. When a device plugin or a DRA claim makes that
choice, the chart must not emit it. The default stays as it was, so MIG and
every existing values file are unaffected.

rtvi-vlm could already drop it, but only through gpuResourceName, which also
renames the GPU resource; those are now separate. video-summarization no longer
requests a GPU or sets the variable (it runs on CPU), so it has no such flag.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

SERVICES = Path(__file__).resolve().parents[1] / "helm" / "services"
VLM = SERVICES / "rtvi" / "charts" / "rtvi-vlm"
VAR = "NVIDIA_VISIBLE_DEVICES"
# rtvi-vlm indexes global.ngcApiSecret unconditionally; without it the chart
# does not render at all and every assertion below would pass vacuously.
VLM_GLOBALS = ("global.ngcApiSecret.name=ngc", "global.ngcApiSecret.key=key")

helm_required = unittest.skipUnless(
    shutil.which("helm"), "helm is not installed; chart rendering cannot be checked"
)


def _render(chart: Path, *overrides: str) -> list[dict]:
    command = ["helm", "template", "t", str(chart), "--set", "enabled=true"]
    for value in overrides:
        command += ["--set", value]
    out = subprocess.run(command, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    documents = [d for d in yaml.safe_load_all(out.stdout) if d]
    assert documents, "the chart rendered nothing; the assertions would be vacuous"
    return documents


def _containers(documents: list[dict]):
    for doc in documents:
        spec = (doc.get("spec") or {}).get("template", {}).get("spec")
        if spec:
            yield from spec.get("containers") or []


def _device_values(documents: list[dict]) -> list[str]:
    return [e.get("value") for c in _containers(documents)
            for e in c.get("env") or [] if e.get("name") == VAR]


def _gpu_resources(documents: list[dict]) -> set[str]:
    return {key for c in _containers(documents)
            for section in ("limits", "requests")
            for key in ((c.get("resources") or {}).get(section) or {})
            if key.startswith("nvidia.com/")}


@helm_required
class RtviVlm(unittest.TestCase):
    def test_the_default_render_is_unchanged(self):
        documents = _render(VLM, *VLM_GLOBALS)
        self.assertEqual(_device_values(documents), ["all"])
        self.assertEqual(_gpu_resources(documents), {"nvidia.com/gpu"})

    def test_the_flag_drops_the_variable_without_renaming_the_resource(self):
        """The case gpuResourceName could not express: the platform picks the
        card, and the chart still asks for the ordinary counted GPU."""
        documents = _render(VLM, *VLM_GLOBALS, "pluginSelectsDevices=true")
        self.assertEqual(_device_values(documents), [])
        self.assertEqual(_gpu_resources(documents), {"nvidia.com/gpu"})

    def test_gpu_resource_name_still_implies_the_flag(self):
        documents = _render(VLM, *VLM_GLOBALS, "gpuResourceName=nvidia.com/gpu.eval")
        self.assertEqual(_device_values(documents), [])
        self.assertEqual(_gpu_resources(documents), {"nvidia.com/gpu.eval"})

    def test_a_mig_deployment_is_unaffected(self):
        documents = _render(VLM, *VLM_GLOBALS, "gpuResourceName=nvidia.com/mig-3g.40gb")
        self.assertEqual(_device_values(documents), [])
        self.assertEqual(_gpu_resources(documents), {"nvidia.com/mig-3g.40gb"})

    def test_both_values_together_agree(self):
        documents = _render(VLM, *VLM_GLOBALS, "pluginSelectsDevices=true",
                            "gpuResourceName=nvidia.com/gpu.eval")
        self.assertEqual(_device_values(documents), [])
        self.assertEqual(_gpu_resources(documents), {"nvidia.com/gpu.eval"})


@helm_required
class VideoSummarization(unittest.TestCase):
    def test_it_requests_no_gpu_and_names_none(self):
        """Summarization runs on CPU: no GPU resource, no NVIDIA_VISIBLE_DEVICES,
        so there is nothing for a device plugin or DRA claim to be overridden by."""
        documents = _render(SERVICES / "video-summarization")
        self.assertEqual(_device_values(documents), [])
        self.assertEqual(_gpu_resources(documents), set())


if __name__ == "__main__":
    unittest.main()

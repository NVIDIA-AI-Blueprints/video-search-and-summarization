# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""pluginSelectsDevices drops NVIDIA_VISIBLE_DEVICES, and nothing else does.

The variable names GPUs from inside the container, which overrides whatever
assigned them from outside. When a device plugin or a DRA claim makes that
choice, the chart must not emit it. The default stays as it was, so MIG and
every existing values file are unaffected.

rtvi-vlm could already drop it, but only through gpuResourceName, which also
renames the GPU resource; those are now separate. video-summarization merges
env into a map where extraEnv sets a value and never removes a name, so the
removal has to happen after that merge. LVS delegates inference to RTVI-VLM,
so its default render has no GPU resource or device variable; the flag only
removes a device variable when one is explicitly supplied.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

SERVICES = Path(__file__).resolve().parents[1] / "helm" / "services"
VLM = SERVICES / "rtvi" / "charts" / "rtvi-vlm"
SUMMARIZATION = SERVICES / "video-summarization"
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
    def test_the_default_render_is_unchanged(self):
        documents = _render(SUMMARIZATION)
        self.assertEqual(_device_values(documents), [])
        self.assertEqual(_gpu_resources(documents), set())

    def test_the_flag_drops_the_variable(self):
        self.assertEqual(_device_values(_render(SUMMARIZATION, "pluginSelectsDevices=true")), [])

    def test_extra_env_alone_cannot_remove_it(self):
        """Why the flag exists. extraEnv writes into a map keyed by name, so an
        override replaces the value and the name survives."""
        documents = _render(SUMMARIZATION, f"extraEnv[0].name={VAR}", "extraEnv[0].value=")
        self.assertEqual(_device_values(documents), [""])

    def test_the_flag_wins_over_an_extra_env_entry(self):
        """The removal runs after the extraEnv merge, not before it."""
        documents = _render(SUMMARIZATION, "pluginSelectsDevices=true",
                            f"extraEnv[0].name={VAR}", "extraEnv[0].value=7")
        self.assertEqual(_device_values(documents), [])

    def test_other_env_entries_are_untouched(self):
        overrides = (f"extraEnv[0].name={VAR}", "extraEnv[0].value=7")
        on = _render(SUMMARIZATION, "pluginSelectsDevices=true", *overrides)
        off = _render(SUMMARIZATION, *overrides)
        env = lambda docs: {e["name"]: e for c in _containers(docs) for e in c.get("env") or []}
        before = env(off)
        self.assertEqual(before.pop(VAR)["value"], "7")
        self.assertEqual(env(on), before)


if __name__ == "__main__":
    unittest.main()

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Keep the Nemotron 3.5 Helm runtime aligned with its Compose profiles."""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from functools import cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
HELM_ROOT = REPO_ROOT / "helm"
NIMS_CHART = HELM_ROOT / "services" / "nims"
NEMOTRON_CHART = NIMS_CHART / "charts" / "nemotron-3.5-lightning-30b-a3b"
DOCKER_ENV_DIR = (
    REPO_ROOT / "docker" / "services" / "nim" / "nemotron-3.5-lightning-30b-a3b"
)

MODEL_PROFILE = "2ef85c7286907e706eb0d6c4750a1aefa719447097d151ab34c7837fc02bdac4"
IMAGE_REPOSITORY = "nvcr.io/nim/nvidia/nemotron-3.5-lightning-30b-a3b"
IMAGE_TAG = "2.0.9-variant"

# Intentional Kubernetes-only tuning for an exclusively scheduled 48 GB L40S.
# Compose does not set these values, so it can retain its own allocation policy.
HELM_ONLY_PROFILE_VALUES = {
    "L40S": {"NIM_KVCACHE_PERCENT": "0.8", "NIM_GPU_MEM_FRACTION": "0.8"}
}

helm_required = unittest.skipUnless(
    shutil.which("helm"), "helm is not installed; chart rendering cannot be checked"
)


def _run(cmd: list[str]) -> str:
    env = os.environ.copy()
    # The chart has only file:// dependencies; ignore unrelated repository state.
    env["HELM_REPOSITORY_CONFIG"] = os.devnull
    out = subprocess.run(
        cmd, cwd=HELM_ROOT, env=env, capture_output=True, text=True, check=False
    )
    if out.returncode != 0:
        raise AssertionError(f"{' '.join(cmd)} failed:\n{out.stderr}")
    return out.stdout


def setUpModule() -> None:
    """The NIM umbrella renders only with its file:// dependencies packaged."""
    if shutil.which("helm"):
        _run(["helm", "dependency", "build", "services/nims"])


@cache
def _values() -> dict:
    return yaml.safe_load((NIMS_CHART / "values.yaml").read_text())


@cache
def _nemotron_values() -> dict:
    return yaml.safe_load((NEMOTRON_CHART / "values.yaml").read_text())


def _compose_env(path: Path) -> dict[str, str]:
    values = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


@cache
def _docs(gpu_type: str) -> list[dict]:
    rendered = _run(
        [
            "helm",
            "template",
            "test",
            "services/nims",
            "--set",
            f"gpuType={gpu_type}",
            "--set",
            "nemotron35.fullnameOverride=nemotron-35-lightning-30b-a3b",
        ]
    )
    return [doc for doc in yaml.safe_load_all(rendered) if doc]


def _kind(gpu_type: str, kind: str) -> dict:
    matches = [doc for doc in _docs(gpu_type) if doc.get("kind") == kind]
    if kind == "ConfigMap":
        matches = [
            doc
            for doc in matches
            if doc["metadata"]["name"] == "nemotron-35-lightning-30b-a3b-nim-env"
        ]
    if len(matches) != 1:
        raise AssertionError(
            f"{gpu_type}: expected one Nemotron {kind}, found {len(matches)}"
        )
    return matches[0]


class NemotronProfileSourceParityTests(unittest.TestCase):
    """Helm profiles match Compose except documented platform-specific tuning."""

    def test_all_helm_profiles_match_dedicated_compose_profiles(self):
        for hardware, profile in _values()["gpuProfiles"].items():
            with self.subTest(hardware=hardware):
                compose = _compose_env(DOCKER_ENV_DIR / f"hw-{hardware}.env")
                helm = profile["nemotron35"].copy()
                helm_only = HELM_ONLY_PROFILE_VALUES.get(hardware, {})
                for key, expected in helm_only.items():
                    self.assertEqual(helm.pop(key), expected)
                    self.assertNotIn(key, compose)
                self.assertEqual(helm, compose)

    def test_relevant_shared_compose_profiles_match_helm_too(self):
        for hardware, profile in _values()["gpuProfiles"].items():
            shared = DOCKER_ENV_DIR / f"hw-{hardware}-shared.env"
            if not shared.exists():
                continue
            with self.subTest(hardware=hardware):
                self.assertEqual(profile["nemotron35"], _compose_env(shared))

    def test_every_configmap_key_is_imported_by_the_nimservice(self):
        configured = {
            key
            for profile in _values()["gpuProfiles"].values()
            for key in profile["nemotron35"]
        }
        imported = set(_nemotron_values()["envConfigMapKeys"])
        self.assertEqual(
            configured,
            imported,
            "Nemotron gpuProfiles keys must all reach NIMService.spec.env; "
            "remove stale keys or add them to envConfigMapKeys",
        )


@helm_required
class NemotronProfileRenderTests(unittest.TestCase):
    """The selected profile reaches the rendered NIMService unchanged."""

    def test_every_profile_key_renders_as_an_optional_configmap_reference(self):
        for hardware, profile in _values()["gpuProfiles"].items():
            with self.subTest(hardware=hardware):
                service = _kind(hardware, "NIMService")
                refs = {
                    item["name"]: item["valueFrom"]["configMapKeyRef"]
                    for item in service["spec"]["env"]
                    if "valueFrom" in item
                }
                for key in profile["nemotron35"]:
                    self.assertEqual(refs[key]["key"], key)
                    self.assertEqual(
                        refs[key]["name"],
                        "nemotron-35-lightning-30b-a3b-nim-env",
                    )
                    self.assertIs(refs[key]["optional"], True)

    def test_rtxpro6000bw_runtime_contract(self):
        config = _kind("RTXPRO6000BW", "ConfigMap")["data"]
        service = _kind("RTXPRO6000BW", "NIMService")["spec"]
        env_names = {item["name"] for item in service["env"]}

        self.assertEqual(config["NIM_MODEL_PROFILE"], MODEL_PROFILE)
        self.assertEqual(config["NIM_KVCACHE_PERCENT"], "0.3")
        self.assertEqual(config["NIM_GPU_MEM_FRACTION"], "0.3")
        self.assertEqual(config["NIM_MAX_MODEL_LEN"], "65536")
        self.assertEqual(config["MAX_JOBS"], "4")
        self.assertIn("--max-num-seqs 8", config["NIM_PASSTHROUGH_ARGS"])
        self.assertIn("NIM_MAX_MODEL_LEN", env_names)
        self.assertIn("MAX_JOBS", env_names)

        self.assertEqual(service["storage"]["sharedMemorySizeLimit"], "16Gi")
        self.assertEqual(service["image"]["repository"], IMAGE_REPOSITORY)
        self.assertEqual(service["image"]["tag"], IMAGE_TAG)


if __name__ == "__main__":
    unittest.main()

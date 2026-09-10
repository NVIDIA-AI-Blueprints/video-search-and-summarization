# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Helm LVS registers direct VST camera additions with RT-VLM."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from functools import cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = REPO_ROOT / "helm" / "developer-profiles"
PROFILE = "dev-profile-lvs"
VIOS_COMPONENTS = {"vss-vios-sensor", "vss-vios-streamprocessing"}

helm_required = unittest.skipUnless(
    shutil.which("helm"), "helm is not installed; chart rendering cannot be checked"
)


def _run(cmd: list[str]) -> str:
    out = subprocess.run(
        cmd, cwd=PROFILES_DIR, capture_output=True, text=True, check=False
    )
    if out.returncode != 0:
        raise AssertionError(f"{' '.join(cmd)} failed:\n{out.stderr}")
    return out.stdout


def setUpModule() -> None:
    """The profile renders only with its file:// dependencies unpacked."""
    if shutil.which("helm") and not (PROFILES_DIR / PROFILE / "charts").is_dir():
        _run(["helm", "dependency", "build", PROFILE])


@cache
def _notification_configs(release_prefix: bool = False) -> dict[str, dict]:
    cmd = ["helm", "template", "vss", f"./{PROFILE}"]
    if release_prefix:
        cmd.extend(["--set", "global.useReleaseNamePrefix=true"])

    configs = {}
    for doc in yaml.safe_load_all(_run(cmd)):
        if not doc or doc.get("kind") != "ConfigMap":
            continue
        component = doc.get("metadata", {}).get("labels", {}).get(
            "app.kubernetes.io/name"
        )
        if component in VIOS_COMPONENTS:
            configs[component] = json.loads(doc["data"]["notification_config.json"])
    return configs


@helm_required
class LvsRtviRegistrationTests(unittest.TestCase):
    def test_sensor_and_streamprocessing_share_rtvi_lifecycle_webhooks(self):
        configs = _notification_configs()
        self.assertEqual(set(configs), VIOS_COMPONENTS)

        for component, config in configs.items():
            with self.subTest(component=component):
                self.assertTrue(config["webhooks"]["enabled"])
                events = {
                    item["camera_status_change"]: item
                    for item in config["webhooks"]["items"]
                    if item["enabled"]
                }
                self.assertEqual(
                    set(events), {"camera_add", "camera_streaming", "camera_remove"}
                )
                self.assertEqual(
                    events["camera_add"]["request"][0]["url"],
                    "http://vss-rtvi-vlm:8000/v1/stream/add",
                )
                self.assertEqual(
                    events["camera_streaming"]["request"][0]["url"],
                    "http://vss-rtvi-vlm:8000/v1/stream/add",
                )
                self.assertEqual(
                    events["camera_remove"]["request"][0]["url"],
                    "http://vss-rtvi-vlm:8000/v1/stream/remove",
                )

    def test_release_prefix_is_applied_to_rtvi_webhook_target(self):
        for component, config in _notification_configs(True).items():
            with self.subTest(component=component):
                urls = [
                    request["url"]
                    for item in config["webhooks"]["items"]
                    for request in item["request"]
                ]
                self.assertTrue(
                    all(url.startswith("http://vss-vss-rtvi-vlm:8000/") for url in urls)
                )

    def test_cluster_message_broker_addresses_are_rendered(self):
        for component, config in _notification_configs().items():
            with self.subTest(component=component):
                broker = config["message_broker"]
                self.assertEqual(broker["redis_server_env_var"], "redis:6379")
                self.assertEqual(
                    broker["kafka_server_address"], "kafka-kafka:9092"
                )
                self.assertEqual(
                    broker["mqtt_broker_address"], "tcp://mosquitto:1883"
                )

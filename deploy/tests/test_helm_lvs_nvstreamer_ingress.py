# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""LVS exposes NVStreamer HTTP on its own host, as the Alerts profile does."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from functools import cache
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parents[1] / "helm/developer-profiles/dev-profile-lvs"


@cache
def _deps() -> None:
    result = subprocess.run(
        ["helm", "dependency", "build", "--skip-refresh", str(CHART)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def _render(*overrides: str) -> list[dict]:
    _deps()
    command = ["helm", "template", "review", str(CHART), "-f", str(CHART / "values-lvs.yaml"),
               "--set", "global.externalHost=vss.example.com"]
    for override in overrides:
        command.extend(["--set", override])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def _streamer_rules(documents: list[dict]) -> list[dict]:
    return [rule for document in documents if document["kind"] == "Ingress"
            for rule in document["spec"]["rules"]
            if any("vss-vios-nvstreamer" in path["backend"]["service"]["name"]
                   for path in rule["http"]["paths"])]


@unittest.skipUnless(shutil.which("helm"), "helm is required for chart rendering")
class LvsNvstreamerIngress(unittest.TestCase):
    def test_default_route_targets_the_clusterip_service(self):
        documents = _render()
        rules = _streamer_rules(documents)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["host"], "streamer.vss.example.com")
        path = rules[0]["http"]["paths"][0]
        self.assertEqual((path["path"], path["pathType"]), ("/", "Prefix"))
        service = next(d for d in documents if d["kind"] == "Service"
                       and d["metadata"]["name"] == "vss-vios-nvstreamer")
        self.assertEqual(service["spec"]["type"], "ClusterIP")
        http_port = next(p["port"] for p in service["spec"]["ports"] if p["name"] == "http")
        self.assertEqual(path["backend"]["service"]["port"]["number"], http_port)

    def test_host_override_takes_precedence(self):
        rules = _streamer_rules(_render("vssIngress.streamerHost=media.example.com"))
        self.assertEqual(rules[0]["host"], "media.example.com")

    def test_default_host_follows_main_host_override(self):
        rules = _streamer_rules(_render("vssIngress.host=other.example.com"))
        self.assertEqual(rules[0]["host"], "streamer.other.example.com")

    def test_service_port_override(self):
        documents = _render("vios.vss-vios-nvstreamer.service.port=32000")
        rules = _streamer_rules(documents)
        service = next(d for d in documents if d["kind"] == "Service"
                       and d["metadata"]["name"] == "vss-vios-nvstreamer")
        http_port = next(p["port"] for p in service["spec"]["ports"] if p["name"] == "http")
        self.assertEqual(http_port, 32000)
        self.assertEqual(rules[0]["http"]["paths"][0]["backend"]["service"]["port"]["number"], http_port)

    def test_release_prefix_matches_the_deployed_service(self):
        documents = _render("global.useReleaseNamePrefix=true")
        name = _streamer_rules(documents)[0]["http"]["paths"][0]["backend"]["service"]["name"]
        self.assertEqual(name, "review-vss-vios-nvstreamer")
        self.assertTrue(any(d["kind"] == "Service" and d["metadata"]["name"] == name
                            for d in documents))

    def test_disabled_components_do_not_publish_a_route(self):
        for override in ("vios.enabled=false", "vios.vss-vios-nvstreamer.enabled=false",
                         "vssIngress.enabled=false", "global.externalHost="):
            with self.subTest(override=override):
                self.assertEqual(_streamer_rules(_render(override)), [])


if __name__ == "__main__":
    unittest.main()

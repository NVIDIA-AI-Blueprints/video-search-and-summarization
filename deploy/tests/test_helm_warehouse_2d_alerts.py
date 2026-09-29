# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression checks for warehouse alert routing and chart compatibility."""

import os
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

HELM = os.environ.get("HELM", "helm")
HELM_ROOT = Path(__file__).resolve().parents[1] / "helm"
PROFILE = HELM_ROOT / "industry-profiles/warehouse-operations/warehouse-2d-app"
SERVICES = HELM_ROOT / "services"
HOST = "192.0.2.10"
ALERTS = {
    "global.externalHost": HOST,
    "global.sampleVideoDataset": "nv-warehouse-4cams",
    "vss-alert-bridge.enabled": "true",
    "vss-alert-bridge.kafkaBootstrapServers": "kafka-kafka:9092",
    "vss-alert-bridge.elasticHosts": "elasticsearch:9200",
    "vss-alert-bridge.vstBaseUrl": "http://vss-vios-ingress:30888",
    "vss-agent-ui.enabled": "true",
    "agent.enabled": "true",
    "rtvi.vss-rtvi-vlm.enabled": "true",
}


def render(chart, overrides=None, nodeport=False):
    command = [HELM, "template", "wh", str(chart), "-n", "warehouse-2d"]
    if nodeport:
        command += ["-f", str(PROFILE / "values-nodeport.yaml")]
    for key, value in (overrides or {}).items():
        command += ["--set", f"{key}={value}"]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def resource(docs, kind, name):
    return next(d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name)


def env(workload):
    return {entry["name"]: entry.get("value")
            for entry in workload["spec"]["template"]["spec"]["containers"][0]["env"]}


@unittest.skipUnless(shutil.which(HELM), "Helm is required")
class WarehouseAlertsChartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for chart in (SERVICES / "analytics", PROFILE):
            subprocess.run([HELM, "dependency", "build", "--skip-refresh", str(chart)],
                           capture_output=True, text=True, check=True)
        cls.docs = render(PROFILE, ALERTS, nodeport=True)

    def test_alert_workload_matches_controller_and_profile_overrides(self):
        bridge = resource(self.docs, "StatefulSet", "vss-alert-bridge")
        self.assertEqual(bridge["spec"]["serviceName"], "vss-alert-bridge")
        cfg = yaml.safe_load(resource(self.docs, "ConfigMap", "vss-alert-bridge-config")["data"]["config.yml"])
        self.assertEqual(cfg["vlm"]["response_format"], "json")
        self.assertEqual(cfg["vlm"]["request_timeout"], 120)
        self.assertEqual(cfg["vlm"]["temperature"], 0.6)
        self.assertTrue(cfg["vlm"]["do_resize"])
        self.assertTrue(cfg["vst_config"]["add_overlay"])
        self.assertEqual(cfg["vst_config"]["segment_duration_seconds"], 10)
        self.assertEqual(cfg["alert_agent"]["event_filters"]["dedup_ttl_seconds"], 5)
        self.assertEqual(cfg["alert_agent"]["num_workers"], 10)
        self.assertEqual(cfg["kafka"]["max_poll_records"], 10)
        self.assertTrue(cfg["alert_agent"]["always_on"])
        sdrc = yaml.safe_load(resource(self.docs, "ConfigMap", "sdrc-config")["data"]["config.yml"])
        alert = sdrc["k8s-workload-alerts-2d"]
        self.assertTrue(alert["enable"])
        self.assertEqual(int(alert["WDM_WL_CONFIG_PORT"]), 9080)
        self.assertEqual(yaml.safe_load(alert["WDM_TARGET_PORT_MAPPING"]), {"default": 9080})
        self.assertTrue(alert["WDM_INITIALIZE_FROM_VST"])
        self.assertTrue(alert["WDM_XDS_USE_IP_ADDRESS"])
        self.assertFalse(alert["WDM_XDS_USE_POD_DNS"])
        self.assertEqual(alert["VST_STREAMS_ENDPOINT"], "http://vss-vios-sensor:30000/api/v1/sensor/streams")
        self.assertEqual(env(resource(self.docs, "Deployment", "vss-rtvi-vlm"))["VLM_MODEL_TO_USE"], "cosmos-reason3")

    def test_shared_alert_defaults_unchanged(self):
        docs = render(SERVICES / "alert", {"enabled": "true"})
        resource(docs, "Deployment", "vss-alert-bridge")
        cfg = yaml.safe_load(resource(docs, "ConfigMap", "vss-alert-bridge-config")["data"]["config.yml"])
        self.assertEqual(cfg["vlm"]["response_format"], "cosmos-reason")
        self.assertEqual(cfg["vlm"]["request_timeout"], 5)
        self.assertEqual(cfg["vlm"]["temperature"], 0.6)
        self.assertTrue(cfg["vlm"]["do_resize"])
        self.assertTrue(cfg["vst_config"]["add_overlay"])
        self.assertEqual(cfg["vst_config"]["segment_duration_seconds"], 10)
        self.assertEqual(cfg["alert_agent"]["event_filters"]["dedup_ttl_seconds"], 5)
        self.assertNotIn("warmup", cfg["vlm"])


if __name__ == "__main__":
    unittest.main()

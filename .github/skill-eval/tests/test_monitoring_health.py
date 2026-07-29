# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for freshness-aware coordinator and HA monitoring."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

MONITORING_ROOT = Path(__file__).resolve().parents[1] / "ops" / "monitoring"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


live_dashboard = load_module(
    "test_live_dashboard_module", MONITORING_ROOT / "live_dashboard.py"
)
grafana = load_module(
    "test_grafana_dashboard_module",
    MONITORING_ROOT / "generate_grafana_dashboard.py",
)


def healthy_rows(timestamp: int) -> list[dict]:
    rows = []
    for index in range(1, 9):
        services = {}
        if index <= 3:
            services.update(
                patroni_cluster="healthy",
                etcd_quorum="healthy",
            )
        if index in {4, 5}:
            services.update(
                backup_timer="active",
                restore_test_timer="active",
                backup_result="success",
                restore_test_result="success",
                backup_age_seconds=60,
                restore_test_age_seconds=3600,
            )
        rows.append(
            {
                "host": f"vss-skill-validator-distributed-{index}",
                "status": "online",
                "error": None,
                "metrics": {
                    "collected_at": timestamp,
                    "services": services,
                },
            }
        )
    return rows


class MonitoringHealthTests(unittest.TestCase):
    def test_fleet_health_requires_fresh_hosts_and_deep_ha_signals(self):
        timestamp = 1_800_000_000
        rows = healthy_rows(timestamp)
        self.assertEqual(
            live_dashboard.health_summary(rows, timestamp)["status"],
            "ok",
        )

        rows[0]["metrics"]["services"]["etcd_quorum"] = "unhealthy"
        summary = live_dashboard.health_summary(rows, timestamp)
        self.assertEqual(summary["status"], "degraded")
        self.assertTrue(
            any("etcd quorum" in problem for problem in summary["problems"])
        )

    def test_stale_cached_sample_is_never_healthy(self):
        timestamp = 1_800_000_000
        rows = healthy_rows(timestamp)
        rows[7]["status"] = "stale"
        rows[7]["metrics"]["collected_at"] -= 300

        summary = live_dashboard.health_summary(rows, timestamp)

        self.assertEqual(summary["status"], "degraded")
        self.assertTrue(
            any("distributed-8: stale" in problem for problem in summary["problems"])
        )

    def test_recent_marker_does_not_hide_a_failed_backup(self):
        timestamp = 1_800_000_000
        rows = healthy_rows(timestamp)
        rows[3]["metrics"]["services"]["backup_result"] = "failed"

        summary = live_dashboard.health_summary(rows, timestamp)

        self.assertEqual(summary["status"], "degraded")
        self.assertTrue(
            any("latest backup failed" in item for item in summary["problems"])
        )

    def test_grafana_dashboard_exposes_ha_and_recovery_panels(self):
        dashboard = grafana.dashboard()
        panels = {panel["title"] for panel in dashboard["panels"]}
        self.assertTrue(
            {
                "HA cluster health",
                "Backup and restore health",
                "Backup age",
                "Restore proof age",
            }.issubset(panels)
        )
        dashboard_json = str(dashboard)
        self.assertIn("vss_ha_probe", dashboard_json)
        self.assertIn("range(start: -2m)", dashboard_json)
        self.assertIn('import "array"', dashboard_json)
        self.assertIn('"valid"', dashboard_json)
        self.assertIn("999999999", dashboard_json)
        for panel in dashboard["panels"]:
            if panel["type"] == "stat":
                self.assertEqual(
                    panel["fieldConfig"]["defaults"]["color"]["mode"],
                    "thresholds",
                )


if __name__ == "__main__":
    unittest.main()

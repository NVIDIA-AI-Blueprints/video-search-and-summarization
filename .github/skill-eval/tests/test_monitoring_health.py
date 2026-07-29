# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for freshness-aware coordinator and HA monitoring."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

MONITORING_ROOT = Path(__file__).resolve().parents[1] / "ops" / "monitoring"
POSTGRES_HA_ROOT = MONITORING_ROOT.parent / "postgres-ha"


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
        checked_in_dashboard = json.loads(
            (MONITORING_ROOT / "vss-skill-eval-coordinators.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(checked_in_dashboard, dashboard)
        panels = {panel["title"]: panel for panel in dashboard["panels"]}
        self.assertTrue(
            {
                "HA cluster health",
                "Backup and restore health",
                "Backup age",
                "Restore proof age",
            }.issubset(panels.keys())
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

        ha_coverage = [
            target["query"]
            for target in panels["HA cluster health"]["targets"]
            if 'import "array"' in target["query"]
        ]
        self.assertEqual(len(ha_coverage), 2)
        self.assertTrue(
            any(
                'r._measurement == "vss_ha_cluster"' in query
                and 'r._field == "healthy"' in query
                for query in ha_coverage
            )
        )
        self.assertTrue(
            any(
                'r._measurement == "vss_etcd_quorum"' in query
                and 'r._field == "healthy"' in query
                for query in ha_coverage
            )
        )

        recovery_coverage = [
            target["query"]
            for target in panels["Backup and restore health"]["targets"]
            if 'import "array"' in target["query"]
        ]
        self.assertEqual(len(recovery_coverage), 6)
        expected_recovery_fields = {
            ("vss_ha_unit", "result_success", "backup"),
            ("vss_ha_unit", "result_success", "restore_test"),
            ("vss_ha_unit", "active", "backup_timer"),
            ("vss_ha_unit", "active", "restore_test_timer"),
            ("vss_ha_evidence", "valid", "backup"),
            ("vss_ha_evidence", "valid", "restore_test"),
        }
        for measurement, field, unit in expected_recovery_fields:
            self.assertTrue(
                any(
                    f'r._measurement == "{measurement}"' in query
                    and f'r._field == "{field}"' in query
                    and f'r.unit == "{unit}"' in query
                    for query in recovery_coverage
                ),
                (measurement, field, unit),
            )

        backup_age_coverage = panels["Backup age"]["targets"][1]["query"]
        restore_age_coverage = panels["Restore proof age"]["targets"][1]["query"]
        for query, unit in (
            (backup_age_coverage, "backup"),
            (restore_age_coverage, "restore_test"),
        ):
            self.assertIn('r._measurement == "vss_ha_evidence"', query)
            self.assertIn('r._field == "age_seconds"', query)
            self.assertIn(f'r.unit == "{unit}"', query)
            self.assertNotIn('r._field == "heartbeat"', query)

    def test_backup_registry_uses_first_reachable_database_coordinator(self):
        deployer = (POSTGRES_HA_ROOT / "deploy-postgres-ha-backup.sh").read_text(
            encoding="utf-8"
        )
        registry_block = deployer[
            deployer.index("registry_verified=false") : deployer.index(
                'payload="$(mktemp'
            )
        ]

        self.assertIn("for index in 1 2 3; do", registry_block)
        self.assertIn(
            'registry_host="vss-skill-validator-distributed-${index}"',
            registry_block,
        )
        self.assertIn("if ((ssh_status == 255)); then", registry_block)
        self.assertNotIn("vss-skill-validator-distributed-1", registry_block)


if __name__ == "__main__":
    unittest.main()

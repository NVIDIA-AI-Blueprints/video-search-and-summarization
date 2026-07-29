# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for safe legacy lease migration generation."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "generate_legacy_migration",
    Path(__file__).resolve().parents[1]
    / "ops"
    / "postgres-ha"
    / "generate-legacy-migration.py",
)
generate_legacy_migration = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = generate_legacy_migration
_SPEC.loader.exec_module(generate_legacy_migration)


class GenerateLegacyMigrationTests(unittest.TestCase):
    def test_generated_migration_is_one_time_and_never_clears_ownership(self):
        inventory = [
            {
                "gpu_id": "gpu-a",
                "enabled": True,
                "generation": 7,
                "live": False,
                "metadata": {"pool": "canary"},
            }
        ]

        sql = generate_legacy_migration.build_sql(inventory)

        self.assertIn("legacy-inventory-v1", sql)
        self.assertIn("legacy migration already applied from a different snapshot", sql)
        self.assertIn("LOCK TABLE", sql)
        self.assertIn("refusing unmarked legacy migration", sql)
        self.assertIn("SET generation = 7", sql)
        self.assertIn("(gpu_id, enabled, fence_ready, metadata)", sql)
        self.assertIn("VALUES ('gpu-a', true, false,", sql)
        self.assertNotIn("owner_id = NULL", sql)
        self.assertNotIn("lease_token = NULL", sql)

    def test_loader_rejects_live_or_duplicate_workers(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "inventory.json"
            path.write_text(
                '[{"gpu_id":"gpu-a","generation":1,"live":true}]',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "live lease"):
                generate_legacy_migration.load_inventory(path)

            path.write_text(
                (
                    '[{"gpu_id":"gpu-a","generation":1},'
                    '{"gpu_id":"gpu-a","generation":2}]'
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "duplicate"):
                generate_legacy_migration.load_inventory(path)

    def test_canonical_source_hash_is_stable(self):
        first = [
            {
                "gpu_id": "gpu-a",
                "generation": 2,
                "metadata": {"b": 2, "a": 1},
            }
        ]
        second = [
            {
                "metadata": {"a": 1, "b": 2},
                "generation": 2,
                "gpu_id": "gpu-a",
            }
        ]

        self.assertEqual(
            generate_legacy_migration.build_sql(first),
            generate_legacy_migration.build_sql(second),
        )


if __name__ == "__main__":
    unittest.main()

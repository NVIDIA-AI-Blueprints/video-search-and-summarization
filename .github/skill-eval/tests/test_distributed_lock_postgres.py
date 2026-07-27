#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Integration tests against a real PostgreSQL server.

Set TEST_POSTGRES_DSN to enable:
  TEST_POSTGRES_DSN=postgresql://... python3 test_distributed_lock_postgres.py
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path

SKILL_EVAL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_EVAL_ROOT))

from distributed_lock import PostgresLeaseClient

DSN = os.environ.get("TEST_POSTGRES_DSN", "")


@unittest.skipUnless(DSN, "TEST_POSTGRES_DSN is not set")
class PostgresLeaseIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.psycopg = psycopg
        schema = (SKILL_EVAL_ROOT / "postgres-gpu-leases.sql").read_text()
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(schema)

    def setUp(self):
        with self.psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("TRUNCATE gpu_leases, gpu_workers CASCADE")
            conn.execute(
                """
                INSERT INTO gpu_workers (gpu_id, enabled)
                VALUES ('gpu-a', true), ('gpu-b', true), ('gpu-disabled', false)
                """
            )

    def client(self, owner: str) -> PostgresLeaseClient:
        return PostgresLeaseClient(DSN, owner, ttl_sec=60)

    def test_ordered_acquire_conflict_release_and_generation(self):
        first = self.client("coordinator-1").try_acquire(["gpu-b", "gpu-a"])
        self.assertEqual(first.gpu_id, "gpu-b")
        self.assertEqual(first.generation, 1)

        second = self.client("coordinator-2").try_acquire(["gpu-b", "gpu-a"])
        self.assertEqual(second.gpu_id, "gpu-a")
        self.assertEqual(second.generation, 1)

        self.assertTrue(self.client("coordinator-1").release(first))
        reacquired = self.client("coordinator-3").try_acquire(["gpu-b"])
        self.assertEqual(reacquired.gpu_id, "gpu-b")
        self.assertEqual(reacquired.generation, 2)

    def test_expired_takeover_fences_stale_owner(self):
        stale_client = self.client("stale-owner")
        stale = stale_client.try_acquire(["gpu-a"])
        with self.psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(
                "UPDATE gpu_leases SET "
                "acquired_at = statement_timestamp() - interval '3 seconds', "
                "renewed_at = statement_timestamp() - interval '2 seconds', "
                "lease_expires_at = statement_timestamp() - interval '1 second' "
                "WHERE gpu_id = 'gpu-a'"
            )

        current = self.client("current-owner").try_acquire(["gpu-a"])
        self.assertEqual(current.generation, stale.generation + 1)
        self.assertFalse(stale_client.release(stale))

    def test_disabled_worker_is_never_leased(self):
        self.assertIsNone(self.client("coordinator-1").try_acquire(["gpu-disabled"]))

    def test_concurrent_contenders_have_exactly_one_winner(self):
        barrier = threading.Barrier(16)
        winners = []
        errors = []
        mutex = threading.Lock()

        def contend(index: int) -> None:
            try:
                barrier.wait()
                lease = self.client(f"coordinator-{index}").try_acquire(["gpu-a"])
                if lease is not None:
                    with mutex:
                        winners.append(lease)
            except Exception as exc:  # noqa: BLE001 - collect thread failures
                with mutex:
                    errors.append(exc)

        threads = [threading.Thread(target=contend, args=(i,)) for i in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertFalse(errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(winners), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

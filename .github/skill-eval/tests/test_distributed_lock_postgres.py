#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Integration tests against a real PostgreSQL server.

Set TEST_POSTGRES_DSN to enable:
  TEST_POSTGRES_DSN=postgresql://... python3 test_distributed_lock_postgres.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SKILL_EVAL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_EVAL_ROOT))

from distributed_lock import PostgresLeaseClient
from gpu_fence import FenceController, PostgresLeaseValidator, WorkerCleanup

DSN = os.environ.get("TEST_POSTGRES_DSN", "")


@unittest.skipUnless(DSN, "TEST_POSTGRES_DSN is not set")
class PostgresLeaseIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        cls.psycopg = psycopg
        schema = (SKILL_EVAL_ROOT / "postgres-gpu-leases.sql").read_text()
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles
                        WHERE rolname = 'skill_eval_lease'
                    ) THEN
                        CREATE ROLE skill_eval_lease LOGIN PASSWORD 'test';
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles
                        WHERE rolname = 'skill_eval_fence'
                    ) THEN
                        CREATE ROLE skill_eval_fence LOGIN PASSWORD 'test';
                    END IF;
                END
                $$;
                """
            )
            database = conn.execute("SELECT current_database()").fetchone()[0]
            conn.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO skill_eval_lease").format(
                    sql.Identifier(database)
                )
            )
            conn.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO skill_eval_fence").format(
                    sql.Identifier(database)
                )
            )
            conn.execute(schema)
            # Recreate the previous release's broad grants, then reapply the
            # owner migration and prove it removes them.
            conn.execute(
                """
                GRANT SELECT, INSERT, UPDATE
                ON public.gpu_leases
                TO skill_eval_lease
                """
            )
            conn.execute(schema)
        runtime_params = conninfo_to_dict(DSN)
        runtime_params.update(user="skill_eval_lease", password="test")
        cls.runtime_dsn = make_conninfo(**runtime_params)
        fence_params = conninfo_to_dict(DSN)
        fence_params.update(user="skill_eval_fence", password="test")
        cls.fence_dsn = make_conninfo(**fence_params)

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

    def runtime_client(self, owner: str) -> PostgresLeaseClient:
        return PostgresLeaseClient(self.runtime_dsn, owner, ttl_sec=60)

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

    def test_runtime_role_cannot_forge_rows_but_can_use_fenced_functions(self):
        with (
            self.assertRaises(self.psycopg.errors.InsufficientPrivilege),
            self.psycopg.connect(self.runtime_dsn, autocommit=True) as conn,
        ):
            conn.execute(
                "UPDATE gpu_leases SET owner_id = 'forged' WHERE gpu_id = 'gpu-a'"
            )

        with (
            self.assertRaises(self.psycopg.errors.InsufficientPrivilege),
            self.psycopg.connect(self.runtime_dsn, autocommit=True) as conn,
        ):
            conn.execute("SELECT lease_token FROM gpu_leases")

        client = self.runtime_client("runtime-coordinator")
        lease = client.try_acquire(["gpu-a"])
        self.assertEqual(lease.gpu_id, "gpu-a")
        with self.psycopg.connect(self.runtime_dsn, autocommit=True) as conn:
            status = conn.execute(
                "SELECT owner_id, generation FROM gpu_lease_status "
                "WHERE gpu_id = 'gpu-a'"
            ).fetchone()
        self.assertEqual(status, ("runtime-coordinator", lease.generation))
        renewed = client.renew(lease)
        self.assertGreater(renewed.expires_at, lease.expires_at)
        self.assertTrue(client.release(renewed))

    def test_worker_role_can_only_validate_exact_live_generation(self):
        client = self.runtime_client("runtime-coordinator")
        lease = client.try_acquire(["gpu-a"])

        with self.psycopg.connect(self.fence_dsn, autocommit=True) as conn:
            valid, remaining = conn.execute(
                "SELECT valid, remaining_seconds "
                "FROM public.validate_gpu_lease(%s, %s, %s)",
                (lease.gpu_id, lease.token, lease.generation),
            ).fetchone()
            self.assertTrue(valid)
            self.assertGreater(remaining, 0)

            valid, remaining = conn.execute(
                "SELECT valid, remaining_seconds "
                "FROM public.validate_gpu_lease(%s, gen_random_uuid(), %s)",
                (lease.gpu_id, lease.generation),
            ).fetchone()
            self.assertFalse(valid)
            self.assertEqual(remaining, 0)

            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT lease_token FROM public.gpu_leases")

        self.assertTrue(client.release(lease))
        with self.psycopg.connect(self.fence_dsn, autocommit=True) as conn:
            valid, remaining = conn.execute(
                "SELECT valid, remaining_seconds "
                "FROM public.validate_gpu_lease(%s, %s, %s)",
                (lease.gpu_id, lease.token, lease.generation),
            ).fetchone()
        self.assertFalse(valid)
        self.assertEqual(remaining, 0)

    def test_takeover_kills_stale_worker_process_before_new_session(self):
        stale_client = self.runtime_client("stale-coordinator")
        stale = stale_client.try_acquire(["gpu-a"])
        cleanup_commands = []

        def fake_run(command, **_kwargs):
            cleanup_commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="")

        with tempfile.TemporaryDirectory() as tmp:
            controller = FenceController(
                "gpu-a",
                PostgresLeaseValidator(self.fence_dsn),
                state_path=Path(tmp) / "high-water.json",
                cleanup=WorkerCleanup(
                    termination_grace_sec=0,
                    run=fake_run,
                ),
            )
            stale_session = controller.claim(
                str(stale.token),
                stale.generation,
            )
            process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            try:
                controller.register(stale_session.session_id, process.pid)
                with self.psycopg.connect(DSN, autocommit=True) as conn:
                    conn.execute(
                        "UPDATE gpu_leases SET "
                        "acquired_at = statement_timestamp() - interval '3 seconds', "
                        "renewed_at = statement_timestamp() - interval '2 seconds', "
                        "lease_expires_at = statement_timestamp() - interval '1 second' "
                        "WHERE gpu_id = 'gpu-a'"
                    )
                current_client = self.runtime_client("current-coordinator")
                current = current_client.try_acquire(["gpu-a"])
                current_session = controller.claim(
                    str(current.token),
                    current.generation,
                )
                process.wait(timeout=5)

                self.assertEqual(current.generation, stale.generation + 1)
                self.assertEqual(current_session.generation, current.generation)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn(["docker", "ps", "-aq"], cleanup_commands)
                self.assertTrue(current_client.release(current))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                controller.shutdown()

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

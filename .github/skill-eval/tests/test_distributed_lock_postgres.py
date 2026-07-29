#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Integration tests against a real PostgreSQL server.

Set TEST_POSTGRES_DSN to enable:
  TEST_POSTGRES_DSN=postgresql://... python3 test_distributed_lock_postgres.py
"""

from __future__ import annotations

import importlib.util
import json
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
from gpu_fence import (
    FenceController,
    PostgresLeaseValidator,
    WorkerCleanup,
)

DSN = os.environ.get("TEST_POSTGRES_DSN", "")
_MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "generate_legacy_migration_postgres_test",
    SKILL_EVAL_ROOT / "ops" / "postgres-ha" / "generate-legacy-migration.py",
)
generate_legacy_migration = importlib.util.module_from_spec(_MIGRATION_SPEC)
sys.modules[_MIGRATION_SPEC.name] = generate_legacy_migration
_MIGRATION_SPEC.loader.exec_module(generate_legacy_migration)


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
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles
                        WHERE rolname = 'skill_eval_owner'
                    ) THEN
                        CREATE ROLE skill_eval_owner NOLOGIN;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles
                        WHERE rolname = 'skill_eval_backup'
                    ) THEN
                        CREATE ROLE skill_eval_backup LOGIN PASSWORD 'test';
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
            conn.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO skill_eval_backup").format(
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
            conn.execute(
                """
                GRANT EXECUTE
                ON FUNCTION public.acquire_gpu_lease(
                    text[], text, uuid, integer
                )
                TO skill_eval_backup
                """
            )
            conn.execute(
                """
                ALTER DEFAULT PRIVILEGES IN SCHEMA public
                    GRANT INSERT, UPDATE, DELETE ON TABLES
                    TO skill_eval_backup;
                ALTER DEFAULT PRIVILEGES IN SCHEMA public
                    GRANT USAGE, UPDATE ON SEQUENCES
                    TO skill_eval_backup;
                ALTER DEFAULT PRIVILEGES IN SCHEMA public
                    GRANT EXECUTE ON FUNCTIONS
                    TO skill_eval_backup, PUBLIC;
                ALTER DEFAULT PRIVILEGES
                    GRANT INSERT, UPDATE, DELETE ON TABLES
                    TO skill_eval_backup;
                ALTER DEFAULT PRIVILEGES
                    GRANT USAGE, UPDATE ON SEQUENCES
                    TO skill_eval_backup;
                ALTER DEFAULT PRIVILEGES
                    GRANT EXECUTE ON FUNCTIONS
                    TO skill_eval_backup, PUBLIC;
                """
            )
            conn.execute(schema)
            conn.execute(
                """
                CREATE TABLE public.backup_default_acl_probe (
                    id bigserial PRIMARY KEY,
                    value text
                );
                CREATE FUNCTION public.backup_default_acl_probe()
                RETURNS integer
                LANGUAGE sql
                AS 'SELECT 1';
                """
            )
        runtime_params = conninfo_to_dict(DSN)
        runtime_params.update(user="skill_eval_lease", password="test")
        cls.runtime_dsn = make_conninfo(**runtime_params)
        fence_params = conninfo_to_dict(DSN)
        fence_params.update(user="skill_eval_fence", password="test")
        cls.fence_dsn = make_conninfo(**fence_params)
        backup_params = conninfo_to_dict(DSN)
        backup_params.update(user="skill_eval_backup", password="test")
        cls.backup_dsn = make_conninfo(**backup_params)

    def setUp(self):
        with self.psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(
                "TRUNCATE gpu_leases, gpu_workers, backup_key_registry CASCADE"
            )
            conn.execute(
                """
                INSERT INTO gpu_workers (
                    gpu_id,
                    enabled,
                    fence_ready,
                    fence_version,
                    fence_attested_at
                )
                VALUES
                    ('gpu-a', true, true, '1', statement_timestamp()),
                    ('gpu-b', true, true, '1', statement_timestamp()),
                    ('gpu-disabled', false, false, NULL, NULL)
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

    def test_unattested_worker_cannot_be_acquired_or_renewed(self):
        with self.psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO gpu_workers (gpu_id, enabled)
                VALUES ('gpu-unfenced', true)
                """
            )
        self.assertIsNone(self.client("coordinator-1").try_acquire(["gpu-unfenced"]))

        client = self.client("coordinator-2")
        lease = client.try_acquire(["gpu-a"])
        with self.psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(
                """
                UPDATE gpu_workers
                SET fence_ready = false,
                    fence_version = NULL,
                    fence_attested_at = NULL
                WHERE gpu_id = 'gpu-a'
                """
            )
        with self.assertRaisesRegex(RuntimeError, "lease ownership lost"):
            client.renew(lease)

    def test_legacy_migration_retry_preserves_post_cutover_state(self):
        inventory = [
            {
                "gpu_id": "legacy-gpu",
                "enabled": True,
                "generation": 7,
                "live": False,
                "metadata": {"source": "legacy"},
            }
        ]
        migration_sql = generate_legacy_migration.build_sql(inventory)
        with self.psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("TRUNCATE gpu_leases, gpu_workers CASCADE")
            conn.execute("DROP TABLE IF EXISTS skill_eval_migrations")
            conn.execute(migration_sql)
            migrated = conn.execute(
                """
                SELECT w.enabled, w.fence_ready, l.generation, l.owner_id
                FROM gpu_workers AS w
                JOIN gpu_leases AS l USING (gpu_id)
                WHERE gpu_id = 'legacy-gpu'
                """
            ).fetchone()
            self.assertEqual(migrated, (True, False, 7, None))
            self.assertIsNone(self.client("post-migration").try_acquire(["legacy-gpu"]))

            conn.execute(
                "UPDATE gpu_workers SET enabled = false WHERE gpu_id = 'legacy-gpu'"
            )
            conn.execute(
                """
                INSERT INTO gpu_workers (
                    gpu_id,
                    enabled,
                    fence_ready,
                    fence_version,
                    fence_attested_at
                )
                VALUES (
                    'post-cutover-gpu',
                    true,
                    true,
                    '1',
                    statement_timestamp()
                )
                """
            )
            conn.execute(migration_sql)
            current = conn.execute(
                """
                SELECT gpu_id, enabled, fence_ready
                FROM gpu_workers
                ORDER BY gpu_id
                """
            ).fetchall()
            self.assertEqual(
                current,
                [
                    ("legacy-gpu", False, False),
                    ("post-cutover-gpu", True, True),
                ],
            )

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

    def test_backup_role_is_read_only_and_has_no_elevated_attributes(self):
        with self.psycopg.connect(self.backup_dsn, autocommit=True) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM gpu_workers").fetchone(),
                (3,),
            )
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE gpu_workers SET enabled = false")
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "SELECT * FROM acquire_gpu_lease("
                    "ARRAY['gpu-a'], 'backup', gen_random_uuid(), 60)"
                )
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO backup_default_acl_probe (value) VALUES ('write')"
                )
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT backup_default_acl_probe()")
        with self.psycopg.connect(DSN, autocommit=True) as conn:
            default_acl = conn.execute(
                """
                SELECT
                    has_table_privilege(
                        'skill_eval_backup',
                        'public.backup_default_acl_probe',
                        'SELECT'
                    ),
                    has_table_privilege(
                        'skill_eval_backup',
                        'public.backup_default_acl_probe',
                        'INSERT'
                    ),
                    has_sequence_privilege(
                        'skill_eval_backup',
                        'public.backup_default_acl_probe_id_seq',
                        'SELECT'
                    ),
                    has_sequence_privilege(
                        'skill_eval_backup',
                        'public.backup_default_acl_probe_id_seq',
                        'USAGE'
                    ),
                    has_function_privilege(
                        'skill_eval_backup',
                        'public.backup_default_acl_probe()',
                        'EXECUTE'
                    )
                """
            ).fetchone()
            attributes = conn.execute(
                """
                SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication,
                       rolbypassrls
                FROM pg_roles
                WHERE rolname = 'skill_eval_backup'
                """
            ).fetchone()
        self.assertEqual(default_acl, (True, False, True, False, False))
        self.assertEqual(attributes, (False, False, False, False, False))

    def test_backup_key_fingerprint_is_first_writer_authoritative(self):
        first = "a" * 64
        second = "b" * 64
        with self.psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO backup_key_registry (singleton, sha256)
                VALUES (true, %s)
                ON CONFLICT (singleton) DO NOTHING
                """,
                (first,),
            )
            conn.execute(
                """
                INSERT INTO backup_key_registry (singleton, sha256)
                VALUES (true, %s)
                ON CONFLICT (singleton) DO NOTHING
                """,
                (second,),
            )
            self.assertEqual(
                conn.execute(
                    "SELECT sha256 FROM backup_key_registry WHERE singleton"
                ).fetchone(),
                (first,),
            )

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
            state_path = Path(tmp) / "high-water.json"
            state_path.write_text(
                json.dumps(
                    {
                        "boot_id": Path("/proc/sys/kernel/random/boot_id")
                        .read_text(encoding="utf-8")
                        .strip(),
                        "generation": 0,
                        "process_groups": [],
                    }
                ),
                encoding="utf-8",
            )
            controller = FenceController(
                "gpu-a",
                PostgresLeaseValidator(self.fence_dsn),
                state_path=state_path,
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

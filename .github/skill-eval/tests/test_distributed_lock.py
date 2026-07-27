#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the PostgreSQL GPU lease client."""

from __future__ import annotations

import importlib.util
import sys
import time
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "distributed_lock",
    Path(__file__).resolve().parents[1] / "distributed_lock.py",
)
distributed_lock = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = distributed_lock
_SPEC.loader.exec_module(distributed_lock)


class _Cursor:
    def __init__(self, row, calls):
        self.row = row
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row, calls):
        self.row = row
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return _Cursor(self.row, self.calls)


class _Connect:
    def __init__(self, *rows):
        self.rows = list(rows)
        self.calls = []
        self.connection_calls = []

    def __call__(self, database_url, **kwargs):
        self.connection_calls.append((database_url, kwargs))
        return _Connection(self.rows.pop(0), self.calls)


class PostgresLeaseClientTests(unittest.TestCase):
    def test_acquire_preserves_candidate_order_and_returns_fencing_generation(self):
        token = uuid.uuid4()
        expires = datetime.now(UTC) + timedelta(seconds=90)
        connect = _Connect(("gpu-b", token, 8, expires))
        client = distributed_lock.PostgresLeaseClient(
            "postgresql://lease-db/eval",
            "cpu-1:runner-2:123:456",
            connect=connect,
        )

        lease = client.try_acquire(["gpu-a", "gpu-b", "gpu-a"])

        self.assertEqual(lease.gpu_id, "gpu-b")
        self.assertEqual(lease.generation, 8)
        params = connect.calls[0][1]
        self.assertEqual(params[0], ["gpu-a", "gpu-b"])
        self.assertEqual(params[1], "cpu-1:runner-2:123:456")
        self.assertEqual(params[3], 90)
        self.assertEqual(connect.connection_calls[0][1]["connect_timeout"], 5)
        self.assertIn(
            "statement_timeout=5000", connect.connection_calls[0][1]["options"]
        )
        self.assertIn("lock_timeout=5000", connect.connection_calls[0][1]["options"])

    def test_acquire_returns_none_when_all_candidates_are_leased(self):
        client = distributed_lock.PostgresLeaseClient(
            "postgresql://lease-db/eval", "owner", connect=_Connect(None)
        )
        self.assertIsNone(client.try_acquire(["gpu-a"]))

    def test_empty_candidate_set_does_not_connect(self):
        connect = _Connect()
        client = distributed_lock.PostgresLeaseClient(
            "postgresql://lease-db/eval", "owner", connect=connect
        )
        self.assertIsNone(client.try_acquire([]))
        self.assertEqual(connect.connection_calls, [])

    def test_ttl_and_heartbeat_preserve_shutdown_margin(self):
        with self.assertRaisesRegex(ValueError, "at least 60"):
            distributed_lock.PostgresLeaseClient(
                "postgresql://lease-db/eval", "owner", ttl_sec=59
            )

        lease = distributed_lock.Lease(
            "gpu-a",
            "owner",
            uuid.uuid4(),
            3,
            datetime.now(UTC),
            time.monotonic() + 20,
        )
        client = distributed_lock.PostgresLeaseClient(
            "postgresql://lease-db/eval", "owner", ttl_sec=60
        )
        with self.assertRaisesRegex(ValueError, "safety margin"):
            distributed_lock.LeaseGuard(client, lease, heartbeat_sec=20)

    def test_renew_cannot_resurrect_expired_or_reassigned_lease(self):
        lease = distributed_lock.Lease(
            "gpu-a",
            "owner",
            uuid.uuid4(),
            3,
            datetime.now(UTC),
            time.monotonic() + 90,
        )
        client = distributed_lock.PostgresLeaseClient(
            "postgresql://lease-db/eval", "owner", connect=_Connect(None)
        )
        with self.assertRaises(distributed_lock.LeaseLostError):
            client.renew(lease)

    def test_release_is_owner_token_and_generation_checked(self):
        lease = distributed_lock.Lease(
            "gpu-a",
            "owner",
            uuid.uuid4(),
            3,
            datetime.now(UTC),
            time.monotonic() + 90,
        )
        connect = _Connect(("gpu-a",))
        client = distributed_lock.PostgresLeaseClient(
            "postgresql://lease-db/eval", "owner", connect=connect
        )

        self.assertTrue(client.release(lease))
        self.assertEqual(
            connect.calls[0][1],
            ("gpu-a", "owner", lease.token, 3),
        )

    def test_heartbeat_loss_is_visible_to_process_supervisor(self):
        lease = distributed_lock.Lease(
            "gpu-a",
            "owner",
            uuid.uuid4(),
            3,
            datetime.now(UTC),
            time.monotonic() + 90,
        )
        client = distributed_lock.PostgresLeaseClient(
            "postgresql://lease-db/eval", "owner", ttl_sec=90, connect=_Connect()
        )
        guard = distributed_lock.LeaseGuard(client, lease)
        guard._error = distributed_lock.LeaseLostError("database unavailable")
        guard._lost.set()

        with self.assertRaises(distributed_lock.LeaseLostError):
            guard.raise_if_lost()

    def test_local_deadline_detects_a_stalled_heartbeat(self):
        lease = distributed_lock.Lease(
            "gpu-a",
            "owner",
            uuid.uuid4(),
            3,
            datetime.now(UTC),
            time.monotonic() - 1,
        )
        client = distributed_lock.PostgresLeaseClient(
            "postgresql://lease-db/eval", "owner", ttl_sec=90, connect=_Connect()
        )
        guard = distributed_lock.LeaseGuard(client, lease)

        with self.assertRaisesRegex(
            distributed_lock.LeaseLostError, "local safety deadline"
        ):
            guard.raise_if_lost()


class SqlSafetyTests(unittest.TestCase):
    SCHEMA = (
        Path(__file__).resolve().parents[1] / "postgres-gpu-leases.sql"
    ).read_text()

    def test_acquisition_is_atomic_and_skips_locked_rows(self):
        sql = self.SCHEMA.upper()
        self.assertIn("FOR UPDATE OF L SKIP LOCKED", sql)
        self.assertIn("STATEMENT_TIMESTAMP()", sql)
        self.assertIn("GENERATION = L.GENERATION + 1", sql)

    def test_renewal_requires_unexpired_matching_fence(self):
        sql = self.SCHEMA.upper()
        self.assertIn("LEASE_EXPIRES_AT > STATEMENT_TIMESTAMP()", sql)
        self.assertIn("L.LEASE_TOKEN = REQUESTED_TOKEN", sql)
        self.assertIn("L.GENERATION = REQUESTED_GENERATION", sql)

    def test_runtime_client_uses_security_definer_functions_only(self):
        client_sql = " ".join(
            (
                distributed_lock.ACQUIRE_SQL,
                distributed_lock.RENEW_SQL,
                distributed_lock.RELEASE_SQL,
            )
        ).upper()
        self.assertNotIn("UPDATE GPU_LEASES", client_sql)
        self.assertIn("PUBLIC.ACQUIRE_GPU_LEASE", client_sql)
        self.assertIn("PUBLIC.RENEW_GPU_LEASE", client_sql)
        self.assertIn("PUBLIC.RELEASE_GPU_LEASE", client_sql)
        self.assertGreaterEqual(self.SCHEMA.upper().count("SECURITY DEFINER"), 3)
        self.assertGreaterEqual(
            self.SCHEMA.upper().count("SET SEARCH_PATH = PG_CATALOG"), 3
        )

    def test_owner_schema_migration_is_atomic_and_revokes_legacy_dml(self):
        schema = self.SCHEMA.upper()
        self.assertIn("BEGIN;", schema)
        self.assertTrue(schema.rstrip().endswith("COMMIT;"))
        self.assertIn("CREATE TABLE IF NOT EXISTS PUBLIC.GPU_WORKERS", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS PUBLIC.GPU_LEASES", schema)
        self.assertIn(
            "REVOKE ALL ON TABLE PUBLIC.GPU_WORKERS, PUBLIC.GPU_LEASES", schema
        )
        self.assertIn("WHERE ROLNAME = 'SKILL_EVAL_LEASE'", schema)


if __name__ == "__main__":
    unittest.main(verbosity=2)

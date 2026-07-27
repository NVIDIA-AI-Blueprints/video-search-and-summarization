#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""PostgreSQL-backed leases for sharing GPU workers across coordinators.

The module imports psycopg lazily so coordinators using the legacy local
lock remain dependency-free. PostgreSQL mode is deliberately fail-closed:
an unsuccessful renewal marks the lease unhealthy and the caller must stop
work on the GPU before releasing the lease.
"""

from __future__ import annotations

import dataclasses
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

# run_leg polls every five seconds and allows Harbor 20 seconds to exit before
# SIGKILL. Forty seconds leaves at least 15 seconds of scheduling/kill buffer.
LEASE_SAFETY_MARGIN_SEC = 40

ACQUIRE_SQL = """
WITH candidate AS (
    SELECT l.gpu_id
    FROM gpu_leases AS l
    JOIN gpu_workers AS w USING (gpu_id)
    WHERE l.gpu_id = ANY(%s::text[])
      AND w.enabled
      AND (
          l.owner_id IS NULL
          OR l.lease_expires_at <= statement_timestamp()
      )
    ORDER BY array_position(%s::text[], l.gpu_id)
    FOR UPDATE OF l SKIP LOCKED
    LIMIT 1
)
UPDATE gpu_leases AS l
SET owner_id = %s,
    lease_token = %s,
    generation = l.generation + 1,
    acquired_at = statement_timestamp(),
    renewed_at = statement_timestamp(),
    lease_expires_at = statement_timestamp() + (%s * interval '1 second')
FROM candidate
WHERE l.gpu_id = candidate.gpu_id
RETURNING l.gpu_id, l.lease_token, l.generation, l.lease_expires_at
"""

RENEW_SQL = """
UPDATE gpu_leases
SET renewed_at = statement_timestamp(),
    lease_expires_at = statement_timestamp() + (%s * interval '1 second')
WHERE gpu_id = %s
  AND owner_id = %s
  AND lease_token = %s
  AND generation = %s
  AND lease_expires_at > statement_timestamp()
RETURNING lease_expires_at
"""

RELEASE_SQL = """
UPDATE gpu_leases
SET owner_id = NULL,
    lease_token = NULL,
    renewed_at = statement_timestamp(),
    lease_expires_at = statement_timestamp()
WHERE gpu_id = %s
  AND owner_id = %s
  AND lease_token = %s
  AND generation = %s
RETURNING gpu_id
"""


class LeaseError(RuntimeError):
    """The lease service could not safely complete an operation."""


class LeaseLostError(LeaseError):
    """The lease expired, changed owner, or could not be renewed."""


@dataclasses.dataclass(frozen=True)
class Lease:
    gpu_id: str
    owner_id: str
    token: uuid.UUID
    generation: int
    expires_at: datetime
    deadline_monotonic: float


def _default_connect(database_url: str, **kwargs: Any):
    try:
        import psycopg
    except ImportError as exc:
        raise LeaseError(
            "PostgreSQL locking requires psycopg 3; install "
            "'psycopg[binary]>=3.2,<4' on the coordinator"
        ) from exc
    return psycopg.connect(database_url, **kwargs)


class PostgresLeaseClient:
    """Small transactional client; each operation owns its DB connection."""

    def __init__(
        self,
        database_url: str,
        owner_id: str,
        ttl_sec: int = 90,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        if not database_url:
            raise ValueError("database_url must not be empty")
        if not owner_id:
            raise ValueError("owner_id must not be empty")
        if ttl_sec < 60:
            raise ValueError("ttl_sec must be at least 60 seconds")
        self.database_url = database_url
        self.owner_id = owner_id
        self.ttl_sec = ttl_sec
        self._connect = connect or _default_connect

    def _connection(self):
        return self._connect(
            self.database_url,
            autocommit=False,
            connect_timeout=5,
            options="-c statement_timeout=5000 -c lock_timeout=5000",
        )

    def try_acquire(self, candidates: Sequence[str]) -> Lease | None:
        ordered = list(dict.fromkeys(candidates))
        if not ordered:
            return None
        if any(not item or "\x00" in item for item in ordered):
            raise ValueError("candidate GPU IDs must be non-empty text")

        operation_started = time.monotonic()
        token = uuid.uuid4()
        try:
            with self._connection() as conn, conn.cursor() as cursor:
                cursor.execute(
                    ACQUIRE_SQL,
                    (ordered, ordered, self.owner_id, token, self.ttl_sec),
                )
                row = cursor.fetchone()
        except LeaseError:
            raise
        except Exception as exc:
            raise LeaseError(f"PostgreSQL lease acquisition failed: {exc}") from exc
        if row is None:
            return None
        return Lease(
            gpu_id=row[0],
            owner_id=self.owner_id,
            token=row[1],
            generation=row[2],
            expires_at=row[3],
            deadline_monotonic=(
                operation_started + self.ttl_sec - LEASE_SAFETY_MARGIN_SEC
            ),
        )

    def renew(self, lease: Lease) -> Lease:
        operation_started = time.monotonic()
        try:
            with self._connection() as conn, conn.cursor() as cursor:
                cursor.execute(
                    RENEW_SQL,
                    (
                        self.ttl_sec,
                        lease.gpu_id,
                        lease.owner_id,
                        lease.token,
                        lease.generation,
                    ),
                )
                row = cursor.fetchone()
        except Exception as exc:
            raise LeaseLostError(
                f"could not confirm renewal for {lease.gpu_id}: {exc}"
            ) from exc
        if row is None:
            raise LeaseLostError(f"lease ownership lost or expired for {lease.gpu_id}")
        return dataclasses.replace(
            lease,
            expires_at=row[0],
            deadline_monotonic=(
                operation_started + self.ttl_sec - LEASE_SAFETY_MARGIN_SEC
            ),
        )

    def release(self, lease: Lease) -> bool:
        try:
            with self._connection() as conn, conn.cursor() as cursor:
                cursor.execute(
                    RELEASE_SQL,
                    (
                        lease.gpu_id,
                        lease.owner_id,
                        lease.token,
                        lease.generation,
                    ),
                )
                return cursor.fetchone() is not None
        except Exception as exc:
            raise LeaseError(
                f"PostgreSQL lease release failed for {lease.gpu_id}: {exc}"
            ) from exc


class LeaseGuard:
    """Heartbeat a lease and expose loss to the Harbor process supervisor."""

    def __init__(
        self,
        client: PostgresLeaseClient,
        lease: Lease,
        heartbeat_sec: int = 20,
    ) -> None:
        if (
            heartbeat_sec < 5
            or heartbeat_sec * 2 >= client.ttl_sec
            or heartbeat_sec + LEASE_SAFETY_MARGIN_SEC >= client.ttl_sec
        ):
            raise ValueError(
                "heartbeat_sec must be >= 5 and leave both half the TTL and "
                "the fail-closed safety margin available"
            )
        self.client = client
        self.lease = lease
        self.heartbeat_sec = heartbeat_sec
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(
            target=self._heartbeat,
            name=f"gpu-lease-{lease.gpu_id}",
            daemon=True,
        )

    def start(self) -> LeaseGuard:
        self._thread.start()
        return self

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_sec):
            try:
                self.lease = self.client.renew(self.lease)
            except Exception as exc:  # noqa: BLE001 - surfaced to supervisor
                self._error = exc
                self._lost.set()
                return

    def raise_if_lost(self) -> None:
        if (
            not self._lost.is_set()
            and time.monotonic() >= self.lease.deadline_monotonic
        ):
            self._error = LeaseLostError(
                f"local safety deadline reached for {self.lease.gpu_id}"
            )
            self._lost.set()
        if self._lost.is_set():
            raise LeaseLostError(
                f"lease heartbeat failed for {self.lease.gpu_id}: {self._error}"
            ) from self._error

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=15)
        if self._thread.is_alive():
            self._error = LeaseLostError(
                f"lease heartbeat did not stop for {self.lease.gpu_id}"
            )
            self._lost.set()
        self.raise_if_lost()

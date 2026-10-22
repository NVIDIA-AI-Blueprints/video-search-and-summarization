# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The CLI's job store: ``~/.vision-ai/memory/main.db``, one SQLite file."""

from __future__ import annotations

from contextlib import closing
import json
import os
import sqlite3
import time
from typing import TYPE_CHECKING

from nvidia_vision_ai.memory import JobQuery
from nvidia_vision_ai.memory import JobRecord

if TYPE_CHECKING:
    from pathlib import Path


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS jobs (
        job_id     TEXT PRIMARY KEY,
        type       TEXT NOT NULL,
        status     TEXT NOT NULL,
        created_at TEXT NOT NULL,
        record     TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS jobs_type_created ON jobs (type, created_at DESC)",
)

#: Seconds a connection waits on another process's write lock before failing.
_BUSY_TIMEOUT = 30.0


class SqliteJobStore:
    """A ``nvidia_vision_ai.memory.JobStore`` in one SQLite file, safe for concurrent processes.

    The full record is stored as JSON; ``type``, ``status`` and ``created_at``
    (UTC ISO-8601, so text order is time order) are copied into columns so
    listing by type stays indexed. Writes take ``BEGIN IMMEDIATE`` and wait up
    to 30 s for other writers. A newly created directory and database file are
    owner-only (0700 / 0600): records hold prompts, answers and media URLs.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        if not path.parent.exists():
            path.parent.mkdir(mode=0o700, parents=True)
        if not path.exists():
            os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        self._init_schema()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=_BUSY_TIMEOUT, isolation_level=None)

    def _init_schema(self) -> None:
        # Switching to WAL needs an exclusive lock that the busy handler does not
        # wait for, so concurrent first use retries here instead of failing.
        deadline = time.monotonic() + _BUSY_TIMEOUT
        while True:
            try:
                with closing(self._connect()) as conn:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("BEGIN IMMEDIATE")
                    for statement in _SCHEMA:
                        conn.execute(statement)
                    conn.execute("COMMIT")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

    def put(self, record: JobRecord) -> None:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO jobs (job_id, type, status, created_at, record) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(job_id) DO UPDATE SET type=excluded.type, status=excluded.status, "
                    "created_at=excluded.created_at, record=excluded.record",
                    (
                        record.job_id,
                        record.type,
                        record.status,
                        record.created_at.isoformat(timespec="microseconds"),
                        record.model_dump_json(),
                    ),
                )
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def get(self, job_id: str) -> JobRecord | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT record FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return None if row is None else JobRecord.model_validate(json.loads(row[0]))

    def list(self, query: JobQuery) -> list[JobRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if query.type is not None:
            clauses.append("type = ?")
            params.append(query.type)
        if query.status is not None:
            clauses.append("status = ?")
            params.append(query.status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT record FROM jobs {where} ORDER BY created_at DESC, job_id DESC LIMIT ?"  # noqa: S608
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, (*params, query.limit)).fetchall()
        return [JobRecord.model_validate(json.loads(row[0])) for row in rows]

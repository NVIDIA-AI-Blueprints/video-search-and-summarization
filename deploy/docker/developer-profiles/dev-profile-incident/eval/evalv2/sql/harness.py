# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The SQL harness: the finals schema on a throwaway Postgres, a frozen snapshot loaded, and explicit-parameter calls.

The dashboard and library figures are checked model-free against the production SQL (methodology v2.0 §4 E): the
same migrations the finals project runs (``supabase/migrations/``, applied in order on top of the Supabase stub, as
the schema's own SQL harness does), the rows of a frozen snapshot, and calls whose every parameter is explicit.
``get_incident_dashboard`` falls back to ``CURRENT_DATE`` when its period bounds are null and nothing matches, so a
call without both bounds is refused: a figure must never depend on the day it was computed.

``pgserver`` and ``psycopg`` are not eval dependencies; they are imported when a harness starts, in a throwaway
environment (``scripts/v2_sql_harness.py``). Nothing here reaches the finals database.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from evalv2.core.hashing import sha256_json

PROFILE_DIR = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = PROFILE_DIR / "supabase" / "migrations"
STUB = PROFILE_DIR / "supabase" / "tests" / "supabase_stub.sql"
SNAPSHOT_VERSION = "evalv2-sql-snapshot-v1"
# Load order respects the foreign keys; videos.official_revision_id is set after the revisions exist.
TABLES = ("videos", "model_runs", "report_revisions", "evidence_crops", "analysis_jobs", "queries", "query_results")
DEFERRED = {"videos": ("official_revision_id",)}
DASHBOARD_REQUIRED = ("p_period_start", "p_period_end", "p_timezone", "p_scope")
DASHBOARD_PARAMS = frozenset({*DASHBOARD_REQUIRED, "p_type", "p_severity", "p_day_of_week", "p_hour"})


class SqlHarnessError(ValueError):
    """A malformed snapshot, or a call whose parameters are not all explicit."""


def validate_snapshot(snapshot: Mapping[str, Any]) -> list[str]:
    problems = []
    if snapshot.get("version") != SNAPSHOT_VERSION:
        problems.append(f"snapshot version must be {SNAPSHOT_VERSION!r}")
    try:
        datetime.fromisoformat(str(snapshot.get("taken_at")))
    except ValueError:
        problems.append("taken_at must be an ISO timestamp (the frozen clock)")
    tables = snapshot.get("tables")
    if not isinstance(tables, dict):
        return [*problems, "tables must map table names to row lists"]
    unknown = sorted(set(tables) - set(TABLES))
    if unknown:
        problems.append(f"unknown tables {unknown}")
    for name, rows in tables.items():
        if not isinstance(rows, list) or not all(isinstance(row, dict) and row for row in rows):
            problems.append(f"{name}: rows must be non-empty objects")
    return problems


def snapshot_sha256(snapshot: Mapping[str, Any]) -> str:
    return sha256_json(dict(snapshot))


def dashboard_call(params: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """The SQL and parameters of one dashboard call; refuses an implicit or unknown parameter."""
    missing = [name for name in DASHBOARD_REQUIRED if params.get(name) is None]
    if missing:
        raise SqlHarnessError(f"dashboard calls need explicit {missing}: null period bounds read the clock")
    unknown = sorted(set(params) - DASHBOARD_PARAMS)
    if unknown:
        raise SqlHarnessError(f"unknown dashboard parameters {unknown}")
    for bound in ("p_period_start", "p_period_end"):
        datetime.fromisoformat(str(params[bound]))
    named = ", ".join(f"{key} => %({key})s" for key in sorted(params))
    return f"SELECT public.get_incident_dashboard({named})", dict(params)


def summaries_call(params: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """The SQL of one ``list_incident_report_summaries`` call; page and page size must be explicit."""
    missing = [name for name in ("p_page", "p_page_size") if params.get(name) is None]
    if missing:
        raise SqlHarnessError(f"summary calls need explicit {missing}")
    named = ", ".join(f"{key} => %({key})s" for key in sorted(params))
    return f"SELECT public.list_incident_report_summaries({named})", dict(params)


def _value(value: Any, jsonb: Any) -> Any:
    return jsonb(value) if isinstance(value, dict | list) else value


class SqlHarness:
    """One throwaway database: :meth:`start` it, :meth:`load` a snapshot, then call the functions."""

    def __init__(self, conn: Any, jsonb: Any) -> None:
        self.conn = conn
        self._jsonb = jsonb
        self.snapshot_sha256: str | None = None

    @classmethod
    @contextmanager
    def start(cls, *, migrations: Sequence[Path] | None = None, stub: Path = STUB) -> Iterator[SqlHarness]:
        import pgserver
        import psycopg
        from psycopg.types.json import Jsonb

        files = list(migrations) if migrations is not None else sorted(MIGRATIONS_DIR.glob("*.sql"))
        with tempfile.TemporaryDirectory() as directory:
            server = pgserver.get_server(directory, cleanup_mode="stop")
            conn = psycopg.connect(server.get_uri(), autocommit=True)
            try:
                conn.execute(stub.read_text())
                with conn.transaction():
                    for migration in files:
                        conn.execute(migration.read_text())
                yield cls(conn, Jsonb)
            finally:
                conn.close()

    def load(self, snapshot: Mapping[str, Any]) -> str:
        """Insert every row of the snapshot (as the database owner, past row-level security); returns its hash."""
        problems = validate_snapshot(snapshot)
        if problems:
            raise SqlHarnessError("; ".join(problems))
        tables = snapshot["tables"]
        later: list[tuple[str, dict[str, Any]]] = []
        with self.conn.transaction():
            for table in TABLES:
                for row in tables.get(table, []):
                    deferred = {k: row[k] for k in DEFERRED.get(table, ()) if row.get(k) is not None}
                    values = {k: v for k, v in row.items() if k not in deferred}
                    columns = ", ".join(values)
                    marks = ", ".join(f"%({k})s" for k in values)
                    # Snapshot ids are kept (official_revision_id points at them), identity columns included.
                    sql = f"INSERT INTO public.{table} ({columns}) OVERRIDING SYSTEM VALUE VALUES ({marks})"
                    self.conn.execute(sql, {k: _value(v, self._jsonb) for k, v in values.items()})
                    if deferred:
                        later.append((table, {"id": row["id"], **deferred}))
            for table, update in later:
                sets = ", ".join(f"{k} = %({k})s" for k in update if k != "id")
                self.conn.execute(f"UPDATE public.{table} SET {sets} WHERE id = %(id)s", update)
        self.snapshot_sha256 = snapshot_sha256(snapshot)
        return self.snapshot_sha256

    def _one(self, sql: str, params: Mapping[str, Any]) -> Any:
        row = self.conn.execute(sql, dict(params)).fetchone()
        value = row[0] if row else None
        return json.loads(value) if isinstance(value, str) else value

    def dashboard(self, **params: Any) -> Any:
        sql, values = dashboard_call(params)
        return self._one(sql, values)

    def report_summaries(self, **params: Any) -> Any:
        sql, values = summaries_call(params)
        return self._one(sql, values)

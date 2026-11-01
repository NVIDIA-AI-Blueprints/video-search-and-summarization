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

"""Async Supabase PostgREST helper for the agent's reads and writes against the incident schema.

The finals schema is owned by ``deploy/docker/developer-profiles/dev-profile-incident/supabase/``
(see ``.docs/data.md``); this helper assumes it exists and only reads/writes rows. The agent
touches ``videos`` (read only in production; the console owns that row), ``model_runs`` and the
append-only ``report_revisions`` snapshots. Search reads ``evidence_crops`` and writes ``queries``
and ``query_results`` through its own store (``api/incident_search.py``), using this client.

Every report is a ``report_revisions`` row: ``payload`` holds the full contract report,
``evidence`` the evidence items and their R2 image keys, and the plain facet columns
(``incident_type``, ``severity_level``, ``start_seconds``, ``end_seconds``) are filled by the writer
from the payload. The table rejects UPDATE and DELETE, so this helper only ever inserts revisions.
Human revisions, the official pointer (``videos.official_revision_id``) and the ``evidence_crops``
rows belong to the console.

Configuration is via two environment variables:
- ``INCIDENT_SUPABASE_URL`` — e.g. ``https://<project_ref>.supabase.co``
- ``INCIDENT_SUPABASE_SERVICE_ROLE_KEY`` — the ``service_role`` key (server-side
  only, same secret-handling rigor as the console's ``INCIDENT_DB_DSN`` password).

When either is unset, :func:`is_configured` returns ``False`` and
:func:`get_db` returns ``None`` - callers must treat the incident DB as an
optional feature and degrade gracefully, matching the console's own
"database not configured" stance. There is no fallback DSN.

This module uses ``supabase-py``'s async client (``acreate_client``) rather than
``asyncpg``. The PostgREST layer does not support client-held transactions or
``SELECT ... FOR UPDATE`` row locking; see ``.docs/data.md``'s "Client Access Models
& PostgREST/Direct-Postgres Split" section for why. :meth:`IncidentDB.insert_ai_revision`
therefore reads ``max(revision_no)`` and inserts as two calls, and relies on the
``(video_id, revision_no)`` unique key plus a retry when a concurrent writer takes the same number.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import datetime as _dt
import logging
import math
import os
import random
from typing import TYPE_CHECKING
from typing import Any

from postgrest import APIError
from supabase import AsyncClient
from supabase import acreate_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_SUPABASE_URL_ENV_VAR = "INCIDENT_SUPABASE_URL"
_SUPABASE_KEY_ENV_VAR = "INCIDENT_SUPABASE_SERVICE_ROLE_KEY"
_UNIQUE_VIOLATION = "23505"
_REVISION_INSERT_ATTEMPTS = 5


def _url_from_env() -> str:
    return (os.getenv(_SUPABASE_URL_ENV_VAR) or "").strip()


def _key_from_env() -> str:
    return (os.getenv(_SUPABASE_KEY_ENV_VAR) or "").strip()


def is_configured() -> bool:
    """Whether both Supabase URL and service role key are set."""
    return bool(_url_from_env() and _key_from_env())


def _utcnow() -> _dt.datetime:
    # The console schema uses PostgreSQL ``timestamp without time zone``.
    # Supabase/PostgREST also expects naive UTC for timestamptz-less columns.
    return _dt.datetime.now(_dt.UTC).replace(tzinfo=None)


def _row_to_dict(row: dict[str, Any] | None) -> dict[str, Any] | None:
    return row if row is not None else None


class IncidentDB:
    """Thin async CRUD wrapper around a Supabase async client."""

    def __init__(self, client: AsyncClient):
        self.client = client

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[IncidentDB]:
        """Yield a writer for a sequence of calls sharing no real cross-call atomicity.

        PostgREST has no client-held transactions, so unlike the direct-Postgres
        version this does not roll back earlier calls if a later one in the
        block fails - it exists only so callers written against that sequential
        call-group shape (e.g. the mock backend's Analyze route) keep working.
        """
        yield self

    # -- lifecycle --------------------------------------------------------#
    @classmethod
    async def connect(
        cls,
        url: str | None = None,
        key: str | None = None,
    ) -> IncidentDB:
        """Create a client against ``url``/``key`` (default: env vars).

        Raises ``ValueError`` when credentials are missing — check
        :func:`is_configured` before calling so an unconfigured deployment
        degrades instead of crashing.
        """
        resolved_url = (url or _url_from_env()).strip()
        resolved_key = (key or _key_from_env()).strip()
        if not resolved_url or not resolved_key:
            raise ValueError(
                f"{_SUPABASE_URL_ENV_VAR} and {_SUPABASE_KEY_ENV_VAR} must be set; incident DB is not configured"
            )
        client = await acreate_client(resolved_url, resolved_key)
        return cls(client)

    async def close(self) -> None:
        # supabase-py async client has no explicit close; just drop the reference
        pass

    async def __aenter__(self) -> IncidentDB:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def healthcheck(self) -> tuple[bool, str]:
        try:
            # Use a lightweight head request to check connectivity
            await self.client.table("videos").select("id", count="exact", head=True).execute()  # type: ignore[arg-type]
            return True, "ok"
        except Exception as exc:
            return False, str(exc)

    # -- videos -------------------------------------------------------------#
    async def upsert_video(
        self,
        video_id: str,
        *,
        filepath: str | None = None,
        duration: int | None = None,
        source: str | None = None,
    ) -> str:
        """Insert or update the ``videos`` row for this id (the natural key)."""
        data = {
            "id": video_id,
            "filepath": filepath,
            "duration": duration,
            "source": source,
            "uploaded_datetime": _utcnow().isoformat(),
        }
        # PostgREST upsert via on_conflict
        await self.client.table("videos").upsert(data, on_conflict="id").execute()
        return video_id

    async def get_video(self, video_id: str, *, for_update: bool = False) -> dict[str, Any] | None:
        if for_update:
            # PostgREST has no SELECT ... FOR UPDATE equivalent. No production caller
            # uses this today (see plan §1.4), but we raise rather than silently ignore
            # so a future caller fails loudly instead of racing.
            raise NotImplementedError("for_update is not supported over PostgREST")
        result = await self.client.table("videos").select("*").eq("id", video_id).maybe_single().execute()
        return _row_to_dict(result.data)  # type: ignore[arg-type, union-attr]

    async def update_video(self, video_id: str, *, filepath: str | None) -> None:
        """Point an existing video row at its durable R2 object key."""
        await self.client.table("videos").update({"filepath": filepath}).eq("id", video_id).execute()

    async def list_videos(self) -> list[dict[str, Any]]:
        result = await self.client.table("videos").select("*").order("id").execute()
        return result.data or []  # type: ignore[return-value]

    async def delete_video(self, video_id: str) -> None:
        await self.client.table("videos").delete().eq("id", video_id).execute()

    # -- model_runs -----------------------------------------------------#
    async def insert_model_run(
        self,
        model_run_id: str,
        *,
        model_name: str,
        model_version: str | None = None,
        prompt_version: str | None = None,
        run_datetime: _dt.datetime | None = None,
        notes: str | None = None,
    ) -> str:
        """Insert or update-in-place - never delete-then-insert.

        ``report_revisions.model_run_id`` references this row, so re-registering an
        existing run id must update it rather than delete it.
        """
        data = {
            "id": model_run_id,
            "model_name": model_name,
            "model_version": model_version,
            "prompt_version": prompt_version,
            "run_datetime": (run_datetime or _utcnow()).isoformat(),
            "notes": notes,
        }
        await self.client.table("model_runs").upsert(data, on_conflict="id").execute()
        return model_run_id

    async def get_model_run(self, model_run_id: str) -> dict[str, Any] | None:
        result = await self.client.table("model_runs").select("*").eq("id", model_run_id).maybe_single().execute()
        return _row_to_dict(result.data)  # type: ignore[arg-type, union-attr]

    async def insert_model_run_if_absent(
        self,
        model_run_id: str,
        *,
        model_name: str,
        prompt_version: str | None = None,
        run_datetime: _dt.datetime | None = None,
    ) -> str:
        """Create the ``model_runs`` row only when no row has this id; an existing row is left untouched.

        ``report_revisions.model_run_id`` is a foreign key, so an AI revision needs its run row first.
        In console agent mode the console writes the run's ``notes`` record after the agent returns;
        this placeholder (``notes`` NULL) never overwrites a row the console already wrote.
        """
        data = {
            "id": model_run_id,
            "model_name": model_name,
            "model_version": None,
            "prompt_version": prompt_version,
            "run_datetime": (run_datetime or _utcnow()).isoformat(),
            "notes": None,
        }
        await self.client.table("model_runs").upsert(data, on_conflict="id", ignore_duplicates=True).execute()
        return model_run_id

    # -- report_revisions (append-only report snapshots) -----------------#
    async def next_revision_no(self, video_id: str) -> int:
        """``max(revision_no) + 1`` for this video (1 for its first revision)."""
        result = await (
            self.client.table("report_revisions")
            .select("revision_no")
            .eq("video_id", video_id)
            .order("revision_no", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return int(rows[0]["revision_no"]) + 1 if rows else 1  # type: ignore[index, arg-type, call-overload]

    async def insert_ai_revision(
        self,
        video_id: str,
        *,
        model_run_id: str,
        payload: dict[str, Any],
        evidence: list[dict[str, Any]],
        note: str | None = None,
        embedding: list[float] | None = None,
    ) -> dict[str, Any]:
        """Append a new ``kind='ai'`` revision and return the inserted row (with its ``id``).

        A re-generation is a new branch (``parent_id`` NULL) and copies nothing from earlier
        revisions. The facet columns are filled from ``payload`` (:func:`revision_facets`).
        ``embedding`` is the summary vector (:func:`revision_summary_text`), written in the same
        insert because the table rejects UPDATE; ``None`` stores NULL, which search skips.
        ``revision_no`` is ``max + 1``; a concurrent writer taking the same number makes the
        unique key reject the insert, which is retried with the next number.
        """
        last_exc: Exception | None = None
        for attempt in range(_REVISION_INSERT_ATTEMPTS):
            row = {
                "video_id": video_id,
                "revision_no": await self.next_revision_no(video_id),
                "kind": "ai",
                "model_run_id": model_run_id,
                "parent_id": None,
                "payload": payload,
                "evidence": evidence,
                **revision_facets(payload),
                "embedding": embedding,
                "created_by": None,
                "created_at": _utcnow().isoformat(),
                "note": note,
            }
            try:
                result = await self.client.table("report_revisions").insert(row).execute()
            except APIError as exc:
                if exc.code != _UNIQUE_VIOLATION:
                    raise
                last_exc = exc
                if attempt < _REVISION_INSERT_ATTEMPTS - 1:
                    await asyncio.sleep(0.1 * (attempt + 1) + 0.05 * random.random())
                continue
            inserted = (result.data or [row])[0]
            return dict(inserted)  # type: ignore[arg-type]
        raise RuntimeError(f"could not allocate a revision_no for video {video_id}") from last_exc

    async def get_revision(self, revision_id: int) -> dict[str, Any] | None:
        result = await self.client.table("report_revisions").select("*").eq("id", revision_id).maybe_single().execute()
        return _row_to_dict(result.data if result is not None else None)  # type: ignore[arg-type]

    async def list_revisions(self, video_id: str) -> list[dict[str, Any]]:
        result = await (
            self.client.table("report_revisions").select("*").eq("video_id", video_id).order("revision_no").execute()
        )
        return result.data or []  # type: ignore[return-value]

    async def get_revisions(self, revision_ids: list[int]) -> list[dict[str, Any]]:
        """The revisions with these ids, in one request (none asked: no request)."""
        if not revision_ids:
            return []
        result = await self.client.table("report_revisions").select("*").in_("id", revision_ids).execute()
        return result.data or []  # type: ignore[return-value]

    async def newest_revision_ids(self, video_ids: list[str]) -> dict[str, int]:
        """Each video's newest revision id (highest ``revision_no``), in one request; a video with none is absent."""
        if not video_ids:
            return {}
        result = await (
            self.client.table("report_revisions").select("id,video_id,revision_no").in_("video_id", video_ids).execute()
        )
        newest: dict[str, tuple[int, int]] = {}
        for row in result.data or []:
            if not isinstance(row, dict):
                continue
            key, rank = str(row["video_id"]), int(row.get("revision_no") or 0)
            if key not in newest or rank > newest[key][0]:
                newest[key] = (rank, int(row["id"]))
        return {key: revision_id for key, (_, revision_id) in newest.items()}

    async def search_revisions(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 10,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Cosine search over revision summary vectors (``search_report_revisions`` RPC), best first."""
        result = await self.client.rpc(
            "search_report_revisions",
            {"p_query_embedding": query_embedding, "p_top_k": top_k, "p_min_similarity": min_similarity},
        ).execute()
        return result.data or []  # type: ignore[return-value]


def _whole_seconds(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    seconds: float = value
    return round(seconds)


def revision_summary_text(payload: dict[str, Any]) -> str:
    """The text embedded for a revision: ``incident.title`` then ``incident.description``.

    Missing parts are left out; an empty string means there is nothing to embed.
    """
    incident = payload.get("incident") if isinstance(payload, dict) else None
    if not isinstance(incident, dict):
        return ""
    parts = [incident.get("title"), incident.get("description")]
    return "\n".join(part.strip() for part in parts if isinstance(part, str) and part.strip())


def revision_facets(payload: dict[str, Any]) -> dict[str, Any]:
    """The plain facet columns of a ``report_revisions`` row, read from its contract ``payload``.

    ``incident_type`` and ``severity_level`` copy ``incident.type`` and ``incident.severity_level``;
    ``start_seconds`` and ``end_seconds`` are ``incident.start_timestamp`` and ``end_timestamp``
    rounded to whole seconds. A missing or malformed value leaves its column NULL.
    """
    incident = payload.get("incident") if isinstance(payload, dict) else None
    if not isinstance(incident, dict):
        incident = {}
    incident_type = incident.get("type")
    severity = incident.get("severity_level")
    return {
        "incident_type": incident_type if isinstance(incident_type, str) and incident_type else None,
        "severity_level": severity if isinstance(severity, int) and not isinstance(severity, bool) else None,
        "start_seconds": _whole_seconds(incident.get("start_timestamp")),
        "end_seconds": _whole_seconds(incident.get("end_timestamp")),
    }


# --------------------------------------------------------------------------- #
# Module-level lazy singleton, mirroring db.py's own get_db()/reset_cache().
# --------------------------------------------------------------------------- #
_db: IncidentDB | None = None
_db_lock = asyncio.Lock()


async def get_db() -> IncidentDB | None:
    """Return the shared :class:`IncidentDB`, or ``None`` when unconfigured/unreachable.

    A missing ``INCIDENT_SUPABASE_URL``/``INCIDENT_SUPABASE_SERVICE_ROLE_KEY`` or a
    failed client creation is swallowed so callers can treat the incident DB as
    an optional feature, matching ``db.py``'s own "database not configured"
    degrade-gracefully contract.
    """
    global _db
    if _db is not None:
        return _db
    if not is_configured():
        return None
    async with _db_lock:
        # mypy can't see that another coroutine may have set `_db` while this one
        # awaited the lock, so it treats the re-check as always-None from the
        # first guard above; it is reachable at runtime under real concurrency.
        if _db is not None:
            return _db  # type: ignore[unreachable]
        try:
            _db = await IncidentDB.connect()
        except Exception as exc:
            logger.warning("Incident DB unavailable: %s", exc)
            return None
    return _db


async def reset_cache() -> None:
    """Drop the cached client (used by tests)."""
    global _db
    if _db is not None:
        await _db.close()
    _db = None

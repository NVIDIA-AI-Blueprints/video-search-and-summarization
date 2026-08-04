# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared job lifecycle, ULID minting, and completion-marker helpers.

Every job-capable group (search, summarize, future alerts/vios/vlm) must use
this module rather than duplicating identity, persistence, or marker logic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import secrets
import sys
import time
from typing import Any
from typing import TextIO

from vss_core.memory.adapters import MemoryAdapter
from vss_core.memory.adapters import utc_now_iso
from vss_core.memory.models import JobStatus
from vss_core.memory.models import MemoryError
from vss_core.memory.models import MemoryGroup
from vss_core.memory.models import MemoryInput
from vss_core.memory.models import MemoryOutput
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.service import MemoryService

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_INVALID_INPUT = 2
EXIT_BACKEND_UNREACHABLE = 3
EXIT_CONFIG_ERROR = 4
EXIT_HANDLE_NOT_FOUND = 5
EXIT_PARTIAL = 6
EXIT_TIMEOUT = 7

MARKER_COMPLETED = "vss_job_completed"
MARKER_FAILED = "vss_job_failed"
MARKER_TIMEOUT = "vss_job_timeout"
_MARKER_EVENTS = frozenset({MARKER_COMPLETED, MARKER_FAILED, MARKER_TIMEOUT})
_MARKER_MAX_BYTES = 1024
_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Group value on the wire (job.group) → job_id prefix. Matches the schema enum.
GROUP_PREFIX: dict[MemoryGroup, str] = {
    "summary": "summary",
    "search": "search",
    "alert": "alert",
    "media": "media",
    "vlm": "vlm",
}


def ulid() -> str:
    """Lexicographically sortable 26-char ULID (48-bit time + 80-bit random)."""
    value = (int(time.time() * 1000) & ((1 << 48) - 1)) << 80 | secrets.randbits(80)
    return "".join(_CROCKFORD32[(value >> shift) & 0x1F] for shift in range(125, -1, -5))


def mint_job_id(group: MemoryGroup) -> str:
    """Mint ``<group>-<ULID>`` — the memory record identity."""
    prefix = GROUP_PREFIX[group]
    return f"{prefix}-{ulid()}"


def completion_marker(
    event: str,
    *,
    group: MemoryGroup,
    job_id: str,
    status: JobStatus | str,
    persisted: bool,
    exit_hint: int,
    asset_id: str | None = None,
) -> str:
    """Render the final-stdout completion marker as one compact JSON line ≤1 KB."""
    if event not in _MARKER_EVENTS:
        raise ValueError(f"unknown marker event {event!r}")
    marker: dict[str, Any] = {
        "event": event,
        "group": group,
        "job_id": job_id,
        "asset_id": asset_id,
        "status": status,
        "persisted": bool(persisted),
        "exit_hint": int(exit_hint),
    }
    line = json.dumps(marker, separators=(",", ":"))
    if len(line.encode("utf-8")) > _MARKER_MAX_BYTES:
        marker["asset_id"] = None
        line = json.dumps(marker, separators=(",", ":"))
    if len(line.encode("utf-8")) > _MARKER_MAX_BYTES:
        # Absolute last resort: drop optional fields already null-safe.
        raise ValueError("completion marker exceeds 1 KB even after trimming")
    return line


def marker_event_for_status(status: JobStatus | str) -> str:
    if status == "timeout":
        return MARKER_TIMEOUT
    if status in {"failed", "partial"}:
        return MARKER_FAILED if status == "failed" else MARKER_COMPLETED
    if status == "completed":
        return MARKER_COMPLETED
    # submitted/running should not normally emit a completion marker; treat as failed.
    return MARKER_FAILED


def emit_marker_line(marker: str, *, stream: TextIO | None = None) -> None:
    """Write the completion marker as the final stdout line."""
    out = stream if stream is not None else sys.stdout
    out.write(marker + "\n")
    out.flush()


@dataclass(slots=True)
class JobLifecycle:
    """Write-ahead lifecycle helper bound to one job and optional memory service."""

    group: MemoryGroup
    job_id: str
    created_at: str
    adapter: MemoryAdapter
    input_data: MemoryInput
    persist: bool
    service: MemoryService | None
    backend_ref: str | None = None
    persisted: bool = False
    last_record: UnifiedMemoryRecord | None = None

    @classmethod
    def start(
        cls,
        *,
        group: MemoryGroup,
        adapter: MemoryAdapter,
        input_data: MemoryInput,
        persist: bool,
        service: MemoryService | None,
        job_id: str | None = None,
        write_submitted: bool = True,
    ) -> JobLifecycle:
        created = utc_now_iso()
        lifecycle = cls(
            group=group,
            job_id=job_id or mint_job_id(group),
            created_at=created,
            adapter=adapter,
            input_data=input_data,
            persist=persist,
            service=service,
        )
        if persist and write_submitted:
            lifecycle.write_submitted()
        return lifecycle

    def write_submitted(self) -> UnifiedMemoryRecord:
        record = self.adapter.submitted_record(
            job_id=self.job_id,
            created_at=self.created_at,
            input_data=self.input_data,
            backend_ref=self.backend_ref,
        )
        return self._persist(record)

    def write_running(self, *, backend_ref: str | None = None) -> UnifiedMemoryRecord:
        if backend_ref is not None:
            self.backend_ref = backend_ref
        record = self.adapter.running_record(
            job_id=self.job_id,
            created_at=self.created_at,
            input_data=self.input_data,
            backend_ref=self.backend_ref,
        )
        return self._persist(record)

    def write_terminal(
        self,
        *,
        status: JobStatus,
        output: MemoryOutput | None = None,
        error: MemoryError | None = None,
        backend_ref: str | None = None,
    ) -> UnifiedMemoryRecord:
        if backend_ref is not None:
            self.backend_ref = backend_ref
        record = self.adapter.terminal_record(
            job_id=self.job_id,
            created_at=self.created_at,
            status=status,
            input_data=self.input_data,
            output=output,
            error=error,
            backend_ref=self.backend_ref,
        )
        return self._persist(record)

    def write_point_terminal(
        self,
        *,
        status: JobStatus,
        output: MemoryOutput | None = None,
        error: MemoryError | None = None,
    ) -> UnifiedMemoryRecord:
        """Point-call path: a single terminal record (no submitted/running)."""
        return self.write_terminal(status=status, output=output, error=error)

    def _persist(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord:
        self.last_record = record
        if not self.persist or self.service is None:
            self.persisted = False
            return record
        stored = self.service.upsert(record)
        self.last_record = stored
        self.persisted = True
        return stored


class PersistError(Exception):
    """Raised when persistence fails after successful retrieval (exit 6)."""

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


def safe_persist(action: Callable[[], Any]) -> None:
    """Run a persistence action; wrap failures as :class:`PersistError`."""
    try:
        action()
    except PersistError:
        raise
    except Exception as error:
        raise PersistError(str(error), cause=error) from error


__all__ = [
    "EXIT_BACKEND_UNREACHABLE",
    "EXIT_CONFIG_ERROR",
    "EXIT_HANDLE_NOT_FOUND",
    "EXIT_INVALID_INPUT",
    "EXIT_OK",
    "EXIT_PARTIAL",
    "EXIT_TIMEOUT",
    "EXIT_UNEXPECTED",
    "GROUP_PREFIX",
    "MARKER_COMPLETED",
    "MARKER_FAILED",
    "MARKER_TIMEOUT",
    "JobLifecycle",
    "PersistError",
    "completion_marker",
    "emit_marker_line",
    "marker_event_for_status",
    "mint_job_id",
    "safe_persist",
    "ulid",
]

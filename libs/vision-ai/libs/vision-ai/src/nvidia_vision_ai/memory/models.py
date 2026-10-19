# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Job records: one row per tool call, keyed by ``job_id``."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
import secrets
import time
from typing import Annotated
from typing import Any
from typing import Literal

from pydantic import AfterValidator
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

JobStatus = Literal["running", "completed", "failed"]

_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid() -> str:
    value = (int(time.time() * 1000) & ((1 << 48) - 1)) << 80 | secrets.randbits(80)
    return "".join(_CROCKFORD32[(value >> shift) & 0x1F] for shift in range(125, -1, -5))


def new_job_id(job_type: str) -> str:
    """``<type>-<ULID>``: unique, and sorts by creation time within a type."""
    return f"{job_type}-{_ulid()}"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _to_utc(value: datetime) -> datetime:
    """Naive datetimes are taken as UTC; aware ones are converted, so stored order is chronological."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]


class JobError(BaseModel):
    """Why a job failed. ``code`` is a short stable token (e.g. ``backend_unreachable``)."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str


class JobRecord(BaseModel):
    """One tool call. ``input`` and ``result`` are the tool's own JSON; never secrets."""

    model_config = ConfigDict(frozen=True)

    job_id: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1, description="Tool name, e.g. 'vlm'.")
    status: JobStatus
    created_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: JobError | None = None


class JobQuery(BaseModel):
    """Filter for :meth:`JobStore.list`. Results are newest first."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str | None = None
    status: JobStatus | None = None
    limit: int = Field(20, ge=1, le=10_000)

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The ``JobStore`` protocol. Implementations live with their callers."""

from __future__ import annotations

from typing import Protocol
from typing import runtime_checkable

from .models import JobQuery
from .models import JobRecord


@runtime_checkable
class JobStore(Protocol):
    """Where job records live. ``put`` is an upsert keyed by ``job_id``."""

    def put(self, record: JobRecord) -> None: ...

    def get(self, job_id: str) -> JobRecord | None: ...

    def list(self, query: JobQuery) -> list[JobRecord]: ...

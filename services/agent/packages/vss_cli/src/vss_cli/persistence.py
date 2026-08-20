# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared bounded lifecycle-close behavior for CLI job groups."""

from __future__ import annotations

import time
from typing import Any

_TERMINAL_WRITE_ATTEMPTS = 3
_TERMINAL_WRITE_BACKOFF_SECONDS = 0.5


def mark_terminal(
    memory: Any,
    adapter: Any,
    *,
    job_id: str,
    created_at: str,
    input_data: Any,
    status: str,
    message: str,
    attempts: int = _TERMINAL_WRITE_ATTEMPTS,
    backoff_seconds: float = _TERMINAL_WRITE_BACKOFF_SECONDS,
) -> bool:
    """Best-effort terminal parent upsert with a small bounded retry."""
    from vss_core.memory.models import MemoryError

    record = adapter.terminal_record(
        job_id=job_id,
        created_at=created_at,
        status=status,
        input_data=input_data,
        error=MemoryError(code=status, message=message),
    )
    delay = backoff_seconds
    for attempt in range(1, attempts + 1):
        try:
            memory.service.upsert(record)
        except Exception:
            if attempt == attempts:
                return False
            time.sleep(delay)
            delay *= 2
        else:
            return True
    return False


__all__ = ["mark_terminal"]

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Process exit codes, shared by every command. A closed set: harnesses branch on them."""

from __future__ import annotations

from enum import IntEnum


class Exit(IntEnum):
    """Each code tells the caller something different to do next."""

    #: Done; stdout carries the result.
    SUCCESS = 0
    #: Unexpected error. Nothing actionable.
    ERROR = 1
    #: The call itself is wrong (flags, media source, values) -- fix and retry.
    INVALID_INPUT = 2
    #: Missing or invalid configuration (``vision-ai configure <tool>``), or an unusable ~/.vision-ai.
    CONFIGURATION = 3
    #: Backend unreachable or timed out. Infrastructure, not the request; retrying is sane.
    BACKEND_UNREACHABLE = 4
    #: Unknown job_id.
    NOT_FOUND = 5
    #: The backend answered but rejected the request or returned an unusable response.
    BACKEND_ERROR = 6

    @property
    def token(self) -> str:
        """Stable lower-case name, used as ``error.code`` in JSON output."""
        return self.name.lower()

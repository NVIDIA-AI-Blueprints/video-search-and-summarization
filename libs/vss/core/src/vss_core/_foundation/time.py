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
"""ISO 8601 ↔ datetime helpers.

Self-contained so the library carries no cross-package time dependency. The
standard internal format is ISO 8601 in UTC with a trailing ``Z``.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime


def datetime_to_iso8601(dt: datetime) -> str:
    """Convert a datetime to a UTC ISO 8601 string (e.g. '2025-08-25T03:05:55.752Z').

    A naive datetime is assumed to already be UTC; an aware datetime in any other
    zone is converted to UTC. Fractional seconds are preserved when present.
    """
    utc_dt = (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC)
    return utc_dt.isoformat().replace("+00:00", "Z")


def iso8601_to_datetime(timestamp: str) -> datetime:
    """Convert ISO 8601 string (e.g. '2025-08-25T03:05:55.752Z') to datetime."""
    dt = datetime.fromisoformat(utc_timestamp_to_tz_timestamp(timestamp))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def utc_timestamp_to_tz_timestamp(timestamp: str) -> str:
    """'2025-08-25T03:05:55.752Z' -> '2025-08-25T03:05:55.752+00:00'."""
    return timestamp.replace("Z", "+00:00")


def tz_timestamp_to_utc_timestamp(timestamp: str) -> str:
    """'2025-08-25T03:05:55.752+00:00' -> '2025-08-25T03:05:55.752Z'."""
    return timestamp.replace("+00:00", "Z")


def safe_iso8601_to_datetime(s: str | None) -> datetime | None:
    """Parse an ISO-8601 string; return ``None`` instead of raising.

    Thin wrapper around :func:`iso8601_to_datetime` for callers that treat
    bad/empty timestamps as "no value" rather than as an error — used by the
    embed-search result mapping when an upstream document may omit timestamp
    fields entirely.
    """
    if not s:
        return None
    try:
        return iso8601_to_datetime(s)
    except (ValueError, TypeError):
        return None


def iso8601_instants_match(a: str | None, b: str | None) -> bool:
    """Return True when two ISO-8601 timestamps denote the same instant.

    Compares by parsed instant so equivalent spellings match — ``...Z`` vs
    ``...+00:00`` and differing fractional-second widths (``.752Z`` vs
    ``.752000Z``, which a :func:`datetime_to_iso8601` round-trip produces).
    Falls back to exact-string equality only when either side cannot be parsed,
    so non-timestamp sentinels still compare correctly.

    Shared by the embed and attribute exclusion filters so both paths agree on
    what "the same clip" means when suppressing an excluded result on
    re-search: :func:`merge_consecutive_results` reformats a result's
    ``end_time`` via the round-trip above, so an exact-string comparison would
    silently fail to exclude the rejected clip on the embed path.
    """
    if a == b:
        return True
    if not a or not b:
        return False
    da = safe_iso8601_to_datetime(a)
    db = safe_iso8601_to_datetime(b)
    if da is not None and db is not None:
        return da == db
    return False

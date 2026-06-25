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

Ported byte-identical from vss_agents/utils/time_convert.py so the library
is self-contained. Standard internal format is ISO 8601 with trailing 'Z'.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime


def datetime_to_iso8601(dt: datetime) -> str:
    """Convert datetime to ISO 8601 string (e.g. '2025-08-25T03:05:55.752Z')."""
    return tz_timestamp_to_utc_timestamp(dt.isoformat())


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

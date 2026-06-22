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

"""Shared ISO-8601 timestamp helpers.

Alert Agent speaks to several systems that serialize timestamps slightly
differently (Kafka publishes an ISO-8601 string with a ``Z`` suffix, the
datetime module produces ``+00:00`` strings, ES indexes nanoseconds, etc.).
This module centralizes the single ``datetime.fromisoformat`` call we use
everywhere so the parse rules cannot drift across features.
"""

from datetime import datetime
from typing import Optional


def parse_iso_utc(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, normalizing ``Z`` suffixes to ``+00:00``.

    Raises ``ValueError`` if the input is not a well-formed ISO-8601 string.
    Raises ``TypeError`` if the input is not a string at all — we let this
    propagate so genuine caller bugs (``None`` where a string was expected)
    surface instead of being silently swallowed.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso_delta_seconds(
    ts_start: Optional[str],
    ts_end: Optional[str],
) -> Optional[float]:
    """Return ``ts_end - ts_start`` in seconds, or ``None`` if unavailable.

    Returns ``None`` when:
      - either input is missing (``None`` or empty string)
      - either input cannot be parsed as ISO-8601 (``ValueError``)
      - the resulting delta is negative (clock-skew guard)

    ``TypeError`` / ``AttributeError`` intentionally propagate: they indicate
    the caller passed a non-string (e.g. a ``datetime`` or a ``dict``) and
    swallowing that would hide a programmer bug.
    """
    if not ts_start or not ts_end:
        return None
    try:
        delta = (parse_iso_utc(ts_end) - parse_iso_utc(ts_start)).total_seconds()
    except ValueError:
        return None
    return delta if delta >= 0 else None

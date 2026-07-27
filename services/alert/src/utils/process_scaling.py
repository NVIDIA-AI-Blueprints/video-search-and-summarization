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

"""Resolution of the pipeline process count from ``alert_agent.processes``."""

import os
from typing import Any, Dict, Optional

PROCESSES_AUTO = "auto"
DEFAULT_PROCESS_COUNT = 1

_ERROR = (
    "alert_agent.processes must be a positive integer or {auto!r}, got {value!r}"
)


def available_cpus() -> int:
    """CPU count the process may actually run on.

    ``sched_getaffinity`` respects cpuset restrictions, so a container pinned
    to 4 of 128 host cores resolves ``auto`` to 4 rather than 128.
    """
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def resolve_process_count(config: Optional[Dict[str, Any]]) -> int:
    """Return the number of pipeline processes to run (>= 1)."""
    raw = (config or {}).get("alert_agent", {}).get("processes", DEFAULT_PROCESS_COUNT)

    if raw is None:
        return DEFAULT_PROCESS_COUNT

    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized == PROCESSES_AUTO:
            return max(1, available_cpus())
        try:
            raw = int(normalized)
        except ValueError:
            raise ValueError(_ERROR.format(auto=PROCESSES_AUTO, value=raw))

    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(_ERROR.format(auto=PROCESSES_AUTO, value=raw))

    return raw

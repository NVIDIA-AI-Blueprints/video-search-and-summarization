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

"""
Human-readable formatting helpers for sizes and durations.

Used by the ``tools/video_utils/*`` CLI tools to keep their pre-flight
and post-flight log blocks compact and consistent.  Lives in
``spatialai_data_utils.visualization.video_utils`` (alongside the
other helpers these CLIs share) rather than the generic ``utils``
package because the only callers today are the video utilities
themselves; promote up to a higher-level package if/when other
parts of the toolkit need them.
"""


def format_size(num_bytes: int) -> str:
    """Format a byte count as the largest unit with 1 decimal place.

    Picks among ``B`` / ``KB`` / ``MB`` / ``GB`` / ``TB``.  Uses
    binary scaling (1024-step) which is the convention used by
    ``ls -lh`` and ``du -h``.

    :param num_bytes: Byte count to format.  Integer or float;
        sub-1024 values format with the ``B`` suffix.
    :return: Formatted string, e.g. ``"3.5 KB"``, ``"1.2 GB"``.
    :rtype: str
    """
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    # Anything that's still ≥ 1024 GB — no further promotion, just
    # report the (potentially > 1024) TB value so the function always
    # terminates without a dead/unreachable branch.
    return f"{num_bytes:.1f} TB"


def format_duration(seconds: float) -> str:
    """Format a wall-time duration with a unit that matches its scale.

    Picks the shortest representation that still conveys the duration:

    * ``< 1s`` → ``"NNN ms"``
    * ``1s ≤ t < 60s`` → ``"S.Ss"`` (one decimal)
    * ``60s ≤ t < 1h`` → ``"MmSSs"``
    * ``≥ 1h`` → ``"HhMMmSSs"``

    :param seconds: Duration in seconds (float).
    :return: Formatted string, e.g. ``"500 ms"``, ``"1.5s"``,
        ``"3m22s"``, ``"1h05m30s"``.
    :rtype: str
    """
    if seconds < 1.0:
        return f"{seconds*1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins, secs = divmod(int(seconds), 60)
    if mins < 60:
        return f"{mins}m{secs:02d}s"
    hours, mins = divmod(mins, 60)
    return f"{hours}h{mins:02d}m{secs:02d}s"

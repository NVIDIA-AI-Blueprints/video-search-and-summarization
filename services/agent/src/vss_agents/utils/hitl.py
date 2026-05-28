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

"""Helpers for rendering Human-in-the-Loop (HITL) popup content."""

import logging

logger = logging.getLogger(__name__)


def format_hitl_popup_header(sensor_ids: list[str] | None, total_videos: int | None) -> str:
    """Build the "which videos does this HITL popup apply to" header.

    Used by tools that collect HITL input for a subset of the user's requested videos
    (e.g. the LVS / base-VLM split in video_report_gen's mixed-routing path).

    - If ``sensor_ids`` is empty/None → no header.
    - If ``total_videos`` is set and larger than ``len(sensor_ids)`` → "Setting prompt
      for X out of Y videos: ..." so the user knows this popup applies to a subset.
    - Otherwise → "Analyzing X video(s): ..." (popup covers all videos in the request).

    If ``total_videos`` is less than ``len(sensor_ids)`` the caller wired it up wrong;
    we log a warning and fall back to the safe "Analyzing N video(s)" wording rather
    than raising, since this helper only renders popup text and must never abort an
    in-progress video analysis over a cosmetic bug.
    """
    if not sensor_ids:
        return ""
    subset = len(sensor_ids)
    if total_videos is not None and total_videos < subset:
        logger.warning(
            "format_hitl_popup_header: total_videos (%d) < len(sensor_ids) (%d); "
            "ignoring total_videos and falling back to 'Analyzing N video(s)' wording. "
            "sensor_ids=%s",
            total_videos,
            subset,
            sensor_ids,
        )
        total_videos = None
    video_list = ", ".join(f"`{sid}`" for sid in sensor_ids)
    if total_videos is not None and total_videos > subset:
        return f"**Setting prompt for {subset} out of {total_videos} videos:** {video_list}\n\n"
    return f"**Analyzing {subset} video(s):** {video_list}\n\n"

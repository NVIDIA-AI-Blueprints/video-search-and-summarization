# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Helpers for NIM media_io_kwargs handling."""

from typing import Optional


def get_frame_sampling_params_from_media_io_kwargs(media_io_kwargs: Optional[dict]) -> dict:
    """Map NIM media_io_kwargs into RTVI frame sampling fields.

    NIM/vLLM uses {"video": {"num_frames": -1}} to sample all frames in the
    submitted video payload. RTVI preserves that value as a fixed-frame sentinel
    so the local frame selector keeps all decoded frames in the chunk.
    """
    if not isinstance(media_io_kwargs, dict):
        return {}

    video_io = media_io_kwargs.get("video", {})
    if not isinstance(video_io, dict):
        return {}

    if "fps" in video_io and video_io["fps"] is not None:
        return {
            "num_frames_per_second_or_fixed_frames_chunk": float(video_io["fps"]),
            "use_fps_for_chunking": True,
        }

    if "num_frames" in video_io and video_io["num_frames"] is not None:
        num_frames = float(video_io["num_frames"])
        return {
            "num_frames_per_second_or_fixed_frames_chunk": num_frames,
            "use_fps_for_chunking": False,
        }

    return {}

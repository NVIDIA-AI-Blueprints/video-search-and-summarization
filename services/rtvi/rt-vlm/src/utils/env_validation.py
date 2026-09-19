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

from __future__ import annotations

import math
import os
import sys


def get_video_pruning_rate() -> float | None:
    """Return the configured EVS pruning rate, rejecting invalid startup values."""
    raw_value = os.environ.get("VLM_VIDEO_PRUNING_RATE", "").strip()
    if not raw_value:
        return None

    try:
        rate = float(raw_value)
    except ValueError as exc:
        raise ValueError(
            "Invalid VLM_VIDEO_PRUNING_RATE: value must be a finite number "
            "greater than 0 and less than 1"
        ) from exc

    if not math.isfinite(rate) or not 0 < rate < 1:
        raise ValueError(
            "Invalid VLM_VIDEO_PRUNING_RATE: value must be a finite number "
            "greater than 0 and less than 1"
        )
    return rate


def validate_rtvi_vlm_environment() -> None:
    get_video_pruning_rate()


if __name__ == "__main__":
    try:
        validate_rtvi_vlm_environment()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

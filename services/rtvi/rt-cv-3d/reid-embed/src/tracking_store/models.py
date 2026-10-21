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

"""Domain models stored by :class:`TrackingStore` backends.

This module is the single source of truth for ``TrackedObject``.

"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel


class TrackedObject(BaseModel):
    """One frame's tracking record for a single object on a single stream."""

    object_id: str
    stream_id: str  # sensor/camera ID
    frame_id: int
    timestamp: str  # Unix timestamp
    leftX: float
    topY: float
    rightX: float
    bottomY: float
    confidence: float
    visibility: Optional[float] = 1.0
    version: Optional[str] = None
    kafka_raw: Optional[Dict[str, Any]] = None
    is_clustered: Optional[bool] = False


__all__ = ["TrackedObject"]

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

"""Data model for cluster statistics.

Lives in its own module (like ``tracking_store.models``) so every
backend and any caller can import the row schema without a circular
dependency on a concrete backend or on ``main.py``.

* :class:`ClusterStats` -- the public Pydantic model (JSON-serialisable
  via the centroid field serializer). This is what the producer
  (maintenance worker / HTTP handlers) hands to a store's
  :meth:`~cluster_stats.base.ClusterStatsStore.replace`.
* :class:`CachedStats` -- the compact internal storage layout returned
  by the read hot path (:meth:`snapshot_for`). The ``object_id`` lives
  in the dict key, not here, so it isn't duplicated per entry.
"""

from __future__ import annotations

from typing import Any, List, NamedTuple

import numpy as np
from pydantic import BaseModel, ConfigDict, field_serializer


class ClusterStats(BaseModel):
    """Per-object aggregated statistics over its embedding cluster."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    object_id: str
    centroid: np.ndarray
    variance: float
    count: int

    @field_serializer("centroid")
    def _serialize_centroid(self, v: np.ndarray, _info: Any) -> List[float]:
        return v.tolist()


class CachedStats(NamedTuple):
    """Internal storage layout for a cluster-stats store.

    Mirrors the data fields of :class:`ClusterStats` but as a typed
    :class:`typing.NamedTuple`: lightweight (the same C-level tuple
    layout under the hood), but with **named fields** so callers don't
    depend on positional ordering. The ``object_id`` lives in the dict
    key, not here, so it isn't duplicated per entry.
    """

    centroid: np.ndarray
    variance: float
    count: int


__all__ = ["ClusterStats", "CachedStats"]

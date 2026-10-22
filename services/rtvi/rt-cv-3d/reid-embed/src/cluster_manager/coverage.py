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

"""Tracking-data coverage check for a clustering batch.

Given the embeddings entering a clustering batch and the set of
``(object_id, sensor_id, frame_id)`` triples for which tracking
information is available, report how many embeddings have no
matching tracking row. Used by the service's maintenance loop to
defer clustering ticks when tracking lags ingest, and usable from
notebook replay harnesses to diagnose missing-tracking data.

Kept in the pure library (no pymilvus, no mp.Queue) so callers from
either the service or the notebook can share the same check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Set

from cluster_manager.types import EmbeddingRecord, TrackingKey


@dataclass(frozen=True, slots=True)
class TrackingCoverage:
    """Result of :func:`tracking_coverage`.

    * ``fully_covered``: ``True`` iff every embedding has a matching
      ``(object_id, sensor_id, frame_id)`` entry in ``available_keys``.
    * ``n_missing``: exact count of embeddings with no matching
      tracking row (may be zero).
    * ``sample_missing_keys``: up to ``sample_limit`` missing keys,
      for diagnostic logging. Order matches the iteration order of
      the input ``embeddings``.
    """

    fully_covered: bool
    n_missing: int
    sample_missing_keys: List[TrackingKey]


def tracking_coverage(
    embeddings: Sequence[EmbeddingRecord],
    available_keys: Set[TrackingKey],
    sample_limit: int = 5,
) -> TrackingCoverage:
    """Return coverage stats for ``embeddings`` against ``available_keys``.

    Pure function; callers build ``available_keys`` from whatever
    tracking-data structure they have. In the service, that's the
    flattened ``object_tracking_db`` keys; in the notebook it's the
    tracking DataFrame's index.
    """
    if sample_limit < 0:
        raise ValueError(f"sample_limit must be >= 0, got {sample_limit!r}")

    missing_sample: List[TrackingKey] = []
    n_missing = 0
    for rec in embeddings:
        key = (int(rec.object_id), str(rec.sensor_id), int(rec.frame_id))
        if key in available_keys:
            continue
        n_missing += 1
        if len(missing_sample) < sample_limit:
            missing_sample.append(key)
    return TrackingCoverage(
        fully_covered=(n_missing == 0),
        n_missing=n_missing,
        sample_missing_keys=missing_sample,
    )

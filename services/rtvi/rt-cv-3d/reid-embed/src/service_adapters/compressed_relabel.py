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

"""Relabel rows in the compressed Milvus collection when clusters merge.

Called after ``ClusterManager.process_batch`` returns so subsequent
per-cluster queries see the merged view.

Uses ``Collection.upsert(...)`` for a single round-trip per batch,
sending FULL rows (all schema fields). Milvus 2.5.x doesn't honor
``partial_update=True`` on the server -- it validates every field's
presence regardless -- so the caller has to supply the unchanged
columns too. The query that finds rows under the retired key fetches
all fields up front to avoid a second round-trip.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional
import logging


def relabel_compressed_clusters(
    compressed_collection: Any,
    retired_to_surviving: Mapping[str, str],
    *,
    batch_size: int,
    logger: Optional[logging.Logger] = None,
    include_mean: bool = True,
) -> int:
    """Rewrite compressed-collection rows labeled by a retired cluster's
    representative tracker_id over to the surviving cluster's rep.

    Args:
        compressed_collection: a loaded ``pymilvus.Collection`` whose schema
            matches ``compressed_embeddings`` (entry_id INT64 PK not auto,
            object_id VARCHAR digit-string, sensor_id VARCHAR,
            frame_id INT64, weight INT64, vector FLOAT_VECTOR, and
            mean only when ``include_mean``). Only object_id is rewritten;
            every other column is carried across as read, so the mean's
            encoding stays opaque here.
        retired_to_surviving: mapping from retired cluster's rep
            (digit-string) to surviving rep (digit-string). The caller is
            expected to resolve cluster_ids to reps via
            ``archive.rep_object_id``.
        batch_size: chunk size for upsert calls.
        logger: optional logger for per-cluster relabel info + errors.
        include_mean: whether the collection carries the per-row ``mean``
            column. False when the service runs without CLT estimation, where
            the column does not exist and naming it would fail the query.

    Returns:
        Total number of rows rewritten across all retired keys.
    """
    if not retired_to_surviving:
        return 0

    row_fields = ["entry_id", "sensor_id", "frame_id", "weight", "vector"]
    if include_mean:
        row_fields.append("mean")

    _log = logger or logging.getLogger(__name__)

    total_rewritten = 0
    for retired_key, surviving_key in retired_to_surviving.items():
        if retired_key == surviving_key:
            # Surviving rep coincided with retired rep (e.g. one cluster
            # absorbed another whose rep already happened to be the
            # lower-min-frame member). Nothing to relabel.
            continue
        try:
            prev = compressed_collection.query(
                expr=f"object_id == '{retired_key}'",
                output_fields=row_fields,
            )
        except Exception as e:
            _log.error(f"Relabel: failed to query compressed for retired key={retired_key}: {e}")
            continue
        if not prev:
            continue
        _log.info(f"Relabel: cluster {retired_key} -> {surviving_key} ({len(prev)} rows)")
        try:
            for i in range(0, len(prev), batch_size):
                batch = prev[i : i + batch_size]
                rows = []
                for item in batch:
                    row = {
                        "entry_id": int(item["entry_id"]),
                        "object_id": surviving_key,
                        "sensor_id": str(item["sensor_id"]),
                        "frame_id": int(item["frame_id"]),
                        "weight": int(item["weight"]),
                        "vector": item["vector"],
                    }
                    if include_mean:
                        row["mean"] = item["mean"]
                    rows.append(row)
                compressed_collection.upsert(rows)
        except Exception as e:
            _log.error(
                f"Relabel: upsert failed for {retired_key} -> "
                f"{surviving_key}: {e}. Some rows may still carry the "
                "retired key."
            )
            continue
        total_rewritten += len(prev)

    return total_rewritten

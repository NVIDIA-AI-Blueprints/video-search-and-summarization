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

"""Tests for :mod:`cluster_manager.coverage`."""

from __future__ import annotations

import numpy as np
import pytest

from cluster_manager.coverage import TrackingCoverage, tracking_coverage
from cluster_manager.types import EmbeddingRecord


def _rec(entry_id: int, obj: int, sensor: str, frame: int) -> EmbeddingRecord:
    return EmbeddingRecord(
        entry_id=entry_id,
        object_id=obj,
        sensor_id=sensor,
        frame_id=frame,
        vector=np.zeros(4, dtype=np.float32),
    )


class TestTrackingCoverage:
    def test_empty_embeddings_is_fully_covered(self) -> None:
        result = tracking_coverage([], set())
        assert isinstance(result, TrackingCoverage)
        assert result.fully_covered is True
        assert result.n_missing == 0
        assert result.sample_missing_keys == []

    def test_all_keys_present(self) -> None:
        recs = [
            _rec(1, 8, "cam1", 100),
            _rec(2, 8, "cam1", 101),
            _rec(3, 9, "cam2", 100),
        ]
        keys = {(8, "cam1", 100), (8, "cam1", 101), (9, "cam2", 100)}
        result = tracking_coverage(recs, keys)
        assert result.fully_covered is True
        assert result.n_missing == 0
        assert result.sample_missing_keys == []

    def test_counts_missing_exactly(self) -> None:
        recs = [
            _rec(1, 8, "cam1", 100),  # present
            _rec(2, 8, "cam1", 101),  # missing
            _rec(3, 9, "cam2", 100),  # missing
            _rec(4, 9, "cam2", 101),  # present
        ]
        keys = {(8, "cam1", 100), (9, "cam2", 101)}
        result = tracking_coverage(recs, keys)
        assert result.fully_covered is False
        assert result.n_missing == 2
        assert set(result.sample_missing_keys) == {(8, "cam1", 101), (9, "cam2", 100)}

    def test_sample_limit_truncates(self) -> None:
        # Ten missing triples; default sample_limit=5 should cap.
        recs = [_rec(i, 8, "cam1", i) for i in range(10)]
        result = tracking_coverage(recs, set())
        assert result.n_missing == 10
        assert len(result.sample_missing_keys) == 5
        # Sample preserves input iteration order.
        assert result.sample_missing_keys == [(8, "cam1", i) for i in range(5)]

    def test_sample_limit_zero_gives_count_only(self) -> None:
        recs = [_rec(i, 8, "cam1", i) for i in range(3)]
        result = tracking_coverage(recs, set(), sample_limit=0)
        assert result.n_missing == 3
        assert result.sample_missing_keys == []

    def test_sample_limit_larger_than_missing(self) -> None:
        recs = [_rec(1, 8, "cam1", 100), _rec(2, 8, "cam1", 101)]
        result = tracking_coverage(recs, set(), sample_limit=100)
        assert result.n_missing == 2
        assert len(result.sample_missing_keys) == 2  # only 2 missing to sample

    def test_negative_sample_limit_raises(self) -> None:
        with pytest.raises(ValueError, match="sample_limit"):
            tracking_coverage([], set(), sample_limit=-1)

    def test_int_coercion_of_frame_and_object(self) -> None:
        # numpy scalar frame_ids or object_ids should still match.
        recs = [
            EmbeddingRecord(
                entry_id=1,
                object_id=np.int64(8),  # numpy scalar on input
                sensor_id="cam1",
                frame_id=np.int64(100),
                vector=np.zeros(4, dtype=np.float32),
            )
        ]
        keys = {(8, "cam1", 100)}
        assert tracking_coverage(recs, keys).fully_covered is True

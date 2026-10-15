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

"""Tests for :mod:`cluster_manager.representatives`."""

from __future__ import annotations

import numpy as np

from cluster_manager.representatives import (
    compute_quality_weighted_centroid,
    compute_subtracklet_representatives,
)
from cluster_manager.types import SubTracklet


class TestComputeQualityWeightedCentroid:
    def test_empty_input_returns_none(self) -> None:
        assert compute_quality_weighted_centroid(np.empty((0, 4))) is None

    def test_single_vector_returns_unit_copy(self) -> None:
        v = np.array([[3.0, 4.0]])
        rep = compute_quality_weighted_centroid(v)
        assert rep is not None
        np.testing.assert_allclose(np.linalg.norm(rep), 1.0, atol=1e-6)

    def test_output_is_unit_vector(self, rng: np.random.Generator) -> None:
        vecs = rng.standard_normal((20, 16)).astype(np.float32)
        vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        rep = compute_quality_weighted_centroid(vecs)
        assert rep is not None
        np.testing.assert_allclose(np.linalg.norm(rep), 1.0, atol=1e-6)

    def test_identical_vectors_collapse_to_themselves(self) -> None:
        axis = np.array([0.0, 0.0, 1.0])
        vecs = np.stack([axis] * 5)
        rep = compute_quality_weighted_centroid(vecs)
        np.testing.assert_allclose(rep, axis, atol=1e-6)

    def test_outlier_gets_downweighted(self) -> None:
        # 5 tightly-clustered vectors around +x axis, one outlier near +y
        tight = [np.array([1.0, 0.05 * i, 0.0]) for i in range(-2, 3)]
        outlier = np.array([0.0, 1.0, 0.0])
        vecs = np.stack([v / np.linalg.norm(v) for v in tight + [outlier]])
        rep = compute_quality_weighted_centroid(vecs)
        # Weighted rep should still point mostly along +x (not dragged to +y)
        assert rep[0] > 0.9
        assert rep[1] < 0.3


class TestComputeSubtrackletRepresentatives:
    def _make_sub(self, name: str, vecs: np.ndarray, *, object_id: int = 1, batch: int = 0) -> SubTracklet:
        n = len(vecs)
        return SubTracklet(
            name=name,
            batch=batch,
            object_id=object_id,
            sub_id=0,
            vecs=vecs.astype(np.float32),
            frame_ids=np.arange(n, dtype=np.int64),
            sensor_ids=["cam1"] * n,
            entry_ids=list(range(n)),
        )

    def test_empty_mapping_returns_empty(self) -> None:
        assert compute_subtracklet_representatives({}, min_embs_threshold=10) == {}

    def test_filters_short_subtracklets(self, rng: np.random.Generator) -> None:
        vecs_short = rng.standard_normal((5, 8))
        vecs_short = vecs_short / np.linalg.norm(vecs_short, axis=1, keepdims=True)
        vecs_long = rng.standard_normal((20, 8))
        vecs_long = vecs_long / np.linalg.norm(vecs_long, axis=1, keepdims=True)
        subs = {
            "short": self._make_sub("short", vecs_short),
            "long": self._make_sub("long", vecs_long),
        }
        reps = compute_subtracklet_representatives(subs, min_embs_threshold=10)
        assert set(reps) == {"long"}

    def test_boundary_exactly_threshold_excluded(self, rng: np.random.Generator) -> None:
        vecs = rng.standard_normal((10, 8))
        vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        subs = {"s": self._make_sub("s", vecs)}
        reps = compute_subtracklet_representatives(subs, min_embs_threshold=10)
        assert reps == {}
        # Strictly above threshold passes
        reps2 = compute_subtracklet_representatives(subs, min_embs_threshold=9)
        assert set(reps2) == {"s"}

    def test_reps_are_unit_vectors(self, rng: np.random.Generator) -> None:
        vecs = rng.standard_normal((15, 8))
        vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        subs = {f"s{i}": self._make_sub(f"s{i}", vecs) for i in range(3)}
        reps = compute_subtracklet_representatives(subs, min_embs_threshold=1)
        for rep in reps.values():
            np.testing.assert_allclose(np.linalg.norm(rep), 1.0, atol=1e-6)

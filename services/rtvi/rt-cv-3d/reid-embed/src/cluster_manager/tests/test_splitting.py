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

"""Tests for :mod:`cluster_manager.splitting`."""

from __future__ import annotations

from typing import List

import numpy as np

from cluster_manager.splitting import split_batch_into_subtracklets


class TestSplitBatchIntoSubtracklets:
    def test_empty_input_returns_empty(self) -> None:
        assert split_batch_into_subtracklets([], batch_idx=0, cos_threshold=0.9) == {}

    def test_single_obj_all_similar_yields_one_subtracklet(self, make_embedding, similar_vectors) -> None:
        vecs = similar_vectors(n=5, spread=0.01)
        records = [
            make_embedding(entry_id=i, object_id=10, sensor_id="cam1", frame_id=i, vector=v) for i, v in enumerate(vecs)
        ]
        result = split_batch_into_subtracklets(records, batch_idx=0, cos_threshold=0.95)
        assert len(result) == 1
        only_name = next(iter(result))
        assert only_name == "b0_10_0"
        assert len(result[only_name].vecs) == 5
        assert result[only_name].object_id == 10
        assert result[only_name].sub_id == 0
        assert result[only_name].batch == 0

    def test_single_obj_split_into_two_on_appearance_change(self, make_embedding, similar_vectors, rng) -> None:
        group_a: List[np.ndarray] = similar_vectors(n=4, spread=0.01)
        # Generate group B in an orthogonal direction from group A
        random_dir = rng.standard_normal(8)
        # Subtract projection onto group A axis so group B is orthogonal
        axis_a = group_a[0]
        ortho = random_dir - (random_dir @ axis_a) * axis_a
        ortho = ortho / np.linalg.norm(ortho)
        group_b: List[np.ndarray] = [ortho + rng.standard_normal(8) * 0.01 for _ in range(4)]
        group_b = [v / np.linalg.norm(v) for v in group_b]

        records = []
        for i, v in enumerate(group_a + group_b):
            records.append(make_embedding(entry_id=i, object_id=7, sensor_id="cam1", frame_id=i, vector=v))
        result = split_batch_into_subtracklets(records, batch_idx=1, cos_threshold=0.9)
        assert len(result) == 2
        sizes = sorted(len(s.vecs) for s in result.values())
        assert sizes == [4, 4]
        # Names are deterministic
        assert "b1_7_0" in result
        assert "b1_7_1" in result

    def test_two_objects_do_not_interfere(self, make_embedding, similar_vectors) -> None:
        vecs_a = similar_vectors(n=3, spread=0.01)
        vecs_b = similar_vectors(n=2, spread=0.01)
        records = [
            make_embedding(entry_id=i, object_id=1, sensor_id="cam1", frame_id=i, vector=v)
            for i, v in enumerate(vecs_a)
        ] + [
            make_embedding(entry_id=100 + i, object_id=2, sensor_id="cam1", frame_id=10 + i, vector=v)
            for i, v in enumerate(vecs_b)
        ]
        result = split_batch_into_subtracklets(records, batch_idx=0, cos_threshold=0.95)
        names = sorted(result)
        assert names == ["b0_1_0", "b0_2_0"]
        assert len(result["b0_1_0"].vecs) == 3
        assert len(result["b0_2_0"].vecs) == 2

    def test_output_sorted_by_frame_id(self, make_embedding, similar_vectors) -> None:
        # Inputs intentionally out of frame order
        vecs = similar_vectors(n=4, spread=0.01)
        frame_ids = [30, 10, 40, 20]
        records = [
            make_embedding(entry_id=i, object_id=5, sensor_id="cam1", frame_id=fi, vector=v)
            for i, (fi, v) in enumerate(zip(frame_ids, vecs))
        ]
        result = split_batch_into_subtracklets(records, batch_idx=0, cos_threshold=0.9)
        only = result["b0_5_0"]
        assert list(only.frame_ids) == [10, 20, 30, 40]
        # entry_ids must track the reordering
        assert only.entry_ids == [1, 3, 0, 2]

    def test_preserves_entry_ids_and_sensor_ids(self, make_embedding, similar_vectors) -> None:
        vecs = similar_vectors(n=3, spread=0.01)
        records = [
            make_embedding(entry_id=111, object_id=1, sensor_id="camA", frame_id=10, vector=vecs[0]),
            make_embedding(entry_id=222, object_id=1, sensor_id="camB", frame_id=11, vector=vecs[1]),
            make_embedding(entry_id=333, object_id=1, sensor_id="camA", frame_id=12, vector=vecs[2]),
        ]
        result = split_batch_into_subtracklets(records, batch_idx=2, cos_threshold=0.9)
        only = result["b2_1_0"]
        assert only.entry_ids == [111, 222, 333]
        assert only.sensor_ids == ["camA", "camB", "camA"]

    def test_threshold_minus_one_absorbs_everything(self, make_embedding, rng) -> None:
        # cos_threshold=-1.0 is the weakest possible bound: every pair
        # qualifies regardless of angle, so even totally unrelated
        # vectors should still collapse into a single sub-tracklet.
        records = []
        for i in range(4):
            v = rng.standard_normal(8).astype(np.float32)
            v = v / np.linalg.norm(v)
            records.append(make_embedding(entry_id=i, object_id=1, sensor_id="cam1", frame_id=i, vector=v))
        result = split_batch_into_subtracklets(records, batch_idx=0, cos_threshold=-1.0)
        assert len(result) == 1

    def test_threshold_above_one_splits_each_embedding(self, make_embedding, similar_vectors) -> None:
        vecs = similar_vectors(n=3, spread=0.01)
        records = [
            make_embedding(entry_id=i, object_id=1, sensor_id="cam1", frame_id=i, vector=v) for i, v in enumerate(vecs)
        ]
        result = split_batch_into_subtracklets(records, batch_idx=0, cos_threshold=1.1)
        # No vector can achieve cos_sim > 1 with any other, so every embedding seeds its own sub
        assert len(result) == 3

    def test_batch_idx_propagates_to_name(self, make_embedding, similar_vectors) -> None:
        vecs = similar_vectors(n=2, spread=0.01)
        records = [
            make_embedding(entry_id=i, object_id=42, sensor_id="cam1", frame_id=i, vector=v) for i, v in enumerate(vecs)
        ]
        result = split_batch_into_subtracklets(records, batch_idx=7, cos_threshold=0.9)
        assert all(name.startswith("b7_") for name in result)

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

"""Tests for :class:`cluster_manager.online_manager.ClusterManager`."""

from __future__ import annotations

from pathlib import Path
from typing import List

import numpy as np
import pytest

from cluster_manager import ClusterManager, ClusterParams, EmbeddingRecord
from config import CLUSTER_ID_START


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def _make_batch(
    *,
    batch_base_entry: int,
    object_id: int,
    sensor_id: str,
    frames: List[int],
    direction: np.ndarray,
    spread: float = 0.01,
    rng: np.random.Generator,
) -> List[EmbeddingRecord]:
    """Create a batch of embeddings clustered around one direction."""
    records: List[EmbeddingRecord] = []
    for i, fid in enumerate(frames):
        noise = rng.standard_normal(len(direction)) * spread
        v = _unit(direction + noise).astype(np.float32)
        records.append(
            EmbeddingRecord(
                entry_id=batch_base_entry + i,
                object_id=object_id,
                sensor_id=sensor_id,
                frame_id=fid,
                vector=v,
            )
        )
    return records


@pytest.fixture
def permissive_params() -> ClusterParams:
    """Parameters tuned for small synthetic tests."""
    return ClusterParams(
        cos_threshold=0.9,
        min_embs_threshold=1,
        coexist_frame_gap=30,
        coexist_min_pairs=3,
        coexist_max_iou=0.1,
        lambda_blend=0.5,
        cos_neighbor_threshold=0.5,
        merge_threshold=0.5,
        blend_strategy="count",
        ema_alpha=0.3,
    )


class TestClusterManagerSingleBatch:
    def test_empty_input_returns_empty_result(self, permissive_params: ClusterParams) -> None:
        manager = ClusterManager(permissive_params)
        result = manager.process_batch([], tracking_data={})
        assert result.stats.n_clusters_total == 0
        assert result.group_info == []
        assert result.entry_to_cluster == {}
        assert len(manager.archive.batch_log) == 1

    def test_single_subtracklet_produces_one_cluster(
        self, permissive_params: ClusterParams, rng: np.random.Generator
    ) -> None:
        manager = ClusterManager(permissive_params)
        direction = rng.standard_normal(8)
        direction = _unit(direction)
        batch = _make_batch(
            batch_base_entry=0,
            object_id=1,
            sensor_id="cam1",
            frames=list(range(0, 10)),
            direction=direction,
            rng=rng,
        )
        result = manager.process_batch(batch, tracking_data={})
        assert result.stats.n_new_clusters == 1
        assert result.stats.n_clusters_total == 1
        assert len(result.entry_to_cluster) == 10

    def test_batch_idx_autoincrements(self, permissive_params: ClusterParams, rng: np.random.Generator) -> None:
        manager = ClusterManager(permissive_params)
        direction = _unit(rng.standard_normal(8))
        for _ in range(3):
            manager.process_batch(
                _make_batch(
                    batch_base_entry=0,
                    object_id=1,
                    sensor_id="cam1",
                    frames=list(range(10)),
                    direction=direction,
                    rng=rng,
                ),
                tracking_data={},
            )
        assert [s.batch for s in manager.archive.batch_log] == [0, 1, 2]


class TestClusterManagerMultiBatch:
    def test_same_person_across_batches_merges_into_one_cluster(
        self, permissive_params: ClusterParams, rng: np.random.Generator
    ) -> None:
        manager = ClusterManager(permissive_params)
        direction = _unit(rng.standard_normal(8))

        batch_a = _make_batch(
            batch_base_entry=0,
            object_id=1,
            sensor_id="cam1",
            frames=list(range(0, 10)),
            direction=direction,
            rng=rng,
        )
        batch_b = _make_batch(
            batch_base_entry=100,
            object_id=2,  # different raw obj id
            sensor_id="cam2",
            frames=list(range(10, 20)),
            direction=direction,  # but same visual direction
            rng=rng,
        )
        manager.process_batch(batch_a, tracking_data={})
        result_b = manager.process_batch(batch_b, tracking_data={})
        # After two batches, a single cluster should persist
        assert result_b.stats.n_clusters_total == 1
        (cluster,) = manager.archive.clusters.values()
        assert cluster.n_embs == 20

    def test_different_people_stay_separate(self, permissive_params: ClusterParams, rng: np.random.Generator) -> None:
        manager = ClusterManager(permissive_params)
        dir_a = _unit(np.array([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]))
        dir_b = _unit(np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]))

        batch = _make_batch(
            batch_base_entry=0,
            object_id=1,
            sensor_id="cam1",
            frames=list(range(0, 10)),
            direction=dir_a,
            rng=rng,
        ) + _make_batch(
            batch_base_entry=100,
            object_id=2,
            sensor_id="cam1",
            frames=list(range(0, 10)),
            direction=dir_b,
            rng=rng,
        )
        result = manager.process_batch(batch, tracking_data={})
        assert result.stats.n_clusters_total == 2


class TestClusterManagerLifecycle:
    def test_reset_clears_archive(self, permissive_params: ClusterParams, rng: np.random.Generator) -> None:
        manager = ClusterManager(permissive_params)
        direction = _unit(rng.standard_normal(8))
        manager.process_batch(
            _make_batch(
                batch_base_entry=0,
                object_id=1,
                sensor_id="cam1",
                frames=list(range(5)),
                direction=direction,
                rng=rng,
            ),
            tracking_data={},
        )
        assert len(manager.archive.clusters) == 1
        manager.reset()
        assert len(manager.archive.clusters) == 0
        assert manager.archive.next_cluster_id == CLUSTER_ID_START
        assert manager.archive.batch_log == []

    def test_save_and_load_roundtrip(
        self,
        permissive_params: ClusterParams,
        rng: np.random.Generator,
        tmp_path: Path,
    ) -> None:
        manager = ClusterManager(permissive_params)
        direction = _unit(rng.standard_normal(8))
        manager.process_batch(
            _make_batch(
                batch_base_entry=0,
                object_id=1,
                sensor_id="cam1",
                frames=list(range(8)),
                direction=direction,
                rng=rng,
            ),
            tracking_data={},
        )
        state_path = tmp_path / "archive.json"
        manager.save_state(state_path)
        assert state_path.exists()

        restored = ClusterManager(permissive_params)
        restored.load_state(state_path)
        assert set(restored.archive.clusters) == set(manager.archive.clusters)
        for cid, info in manager.archive.clusters.items():
            np.testing.assert_allclose(restored.archive.clusters[cid].rep, info.rep)
            assert restored.archive.clusters[cid].n_embs == info.n_embs


class TestSeenEntryIdsTracking:
    def test_populated_for_all_inputs(self, permissive_params: ClusterParams, rng: np.random.Generator) -> None:
        manager = ClusterManager(permissive_params)
        direction = _unit(rng.standard_normal(8))
        batch = _make_batch(
            batch_base_entry=100,
            object_id=1,
            sensor_id="cam1",
            frames=list(range(5)),
            direction=direction,
            rng=rng,
        )
        manager.process_batch(batch, tracking_data={})
        entry_ids = {rec.entry_id for rec in batch}
        assert manager.archive.seen_entry_ids == entry_ids

    def test_superset_of_clustered(self, permissive_params: ClusterParams, rng: np.random.Generator) -> None:
        # Use strict min_embs so one input sub-tracklet gets filtered out
        strict_params = ClusterParams(
            cos_threshold=permissive_params.cos_threshold,
            min_embs_threshold=10,  # any sub with <= 10 is dropped
            coexist_frame_gap=permissive_params.coexist_frame_gap,
            coexist_min_pairs=permissive_params.coexist_min_pairs,
            coexist_max_iou=permissive_params.coexist_max_iou,
            lambda_blend=permissive_params.lambda_blend,
            cos_neighbor_threshold=permissive_params.cos_neighbor_threshold,
            merge_threshold=permissive_params.merge_threshold,
            blend_strategy=permissive_params.blend_strategy,
            ema_alpha=permissive_params.ema_alpha,
        )
        manager = ClusterManager(strict_params)
        direction_big = _unit(rng.standard_normal(8))
        # obj_id=1: 15 embeddings → qualifies
        big = _make_batch(
            batch_base_entry=0,
            object_id=1,
            sensor_id="cam1",
            frames=list(range(15)),
            direction=direction_big,
            rng=rng,
        )
        # obj_id=2: 3 embeddings → dropped
        small = _make_batch(
            batch_base_entry=100,
            object_id=2,
            sensor_id="cam1",
            frames=list(range(3)),
            direction=_unit(rng.standard_normal(8)),
            rng=rng,
        )
        manager.process_batch(big + small, tracking_data={})
        # All inputs are seen
        expected_all = {rec.entry_id for rec in big + small}
        assert manager.archive.seen_entry_ids == expected_all
        # But only qualifying ones were clustered
        assert manager.clustered_entry_ids <= manager.archive.seen_entry_ids
        assert {rec.entry_id for rec in small} & manager.clustered_entry_ids == set()

    def test_accumulates_across_batches(self, permissive_params: ClusterParams, rng: np.random.Generator) -> None:
        manager = ClusterManager(permissive_params)
        direction = _unit(rng.standard_normal(8))
        b1 = _make_batch(
            batch_base_entry=0,
            object_id=1,
            sensor_id="cam1",
            frames=list(range(5)),
            direction=direction,
            rng=rng,
        )
        b2 = _make_batch(
            batch_base_entry=10,
            object_id=1,
            sensor_id="cam1",
            frames=list(range(5, 10)),
            direction=direction,
            rng=rng,
        )
        manager.process_batch(b1, tracking_data={})
        manager.process_batch(b2, tracking_data={})
        expected = {r.entry_id for r in b1 + b2}
        assert manager.archive.seen_entry_ids == expected


class TestClusterManagerIntermediates:
    def test_last_stage_attrs_populated(self, permissive_params: ClusterParams, rng: np.random.Generator) -> None:
        manager = ClusterManager(permissive_params)
        direction = _unit(rng.standard_normal(8))
        manager.process_batch(
            _make_batch(
                batch_base_entry=0,
                object_id=1,
                sensor_id="cam1",
                frames=list(range(10)),
                direction=direction,
                rng=rng,
            ),
            tracking_data={},
        )
        assert manager._last_subtracklets  # sub_tracklets dict non-empty
        assert manager._last_representatives  # reps dict non-empty
        assert manager._last_affinity is not None
        assert manager._last_cluster_members
        assert manager._last_node_meta

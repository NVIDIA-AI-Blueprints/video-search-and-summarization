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

"""Tests for :mod:`cluster_manager.archive`."""

from __future__ import annotations

import numpy as np

from cluster_manager.archive import merge_batch_into_archive
from cluster_manager.types import (
    Archive,
    ClusterInfo,
    ClusterNode,
    MemberHistoryEntry,
    SubTracklet,
)


class TestSeenEntryIds:
    def test_initial_is_empty(self) -> None:
        arch = Archive()
        assert arch.seen_entry_ids == set()

    def test_updates_are_additive(self) -> None:
        arch = Archive()
        arch.seen_entry_ids.update([1, 2, 3])
        arch.seen_entry_ids.update([3, 4])
        assert arch.seen_entry_ids == {1, 2, 3, 4}

    def test_reset_clears_seen(self) -> None:
        arch = Archive()
        arch.seen_entry_ids.update([1, 2, 3])
        arch.reset()
        assert arch.seen_entry_ids == set()


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def _make_sub(name: str, *, vecs: np.ndarray, object_id: int = 1) -> SubTracklet:
    n = len(vecs)
    return SubTracklet(
        name=name,
        batch=0,
        object_id=object_id,
        sub_id=0,
        vecs=vecs.astype(np.float32),
        frame_ids=np.arange(n, dtype=np.int64),
        sensor_ids=["cam1"] * n,
        entry_ids=list(range(1000, 1000 + n)),
    )


def _identity_cluster(cid: int, rep_dir: np.ndarray, n_embs: int = 10) -> ClusterInfo:
    return ClusterInfo(
        cluster_id=cid,
        rep=_unit(rep_dir).astype(np.float32),
        n_embs=n_embs,
        created_batch=0,
        last_updated_batch=0,
        members_history=[
            MemberHistoryEntry(
                sub_name=f"old_{cid}",
                source_batch=0,
                obj_id=cid,
                frame_ids=[0],
                sensor_ids=["cam1"],
                entry_ids=[cid * 100],
            )
        ],
    )


class TestMergeBatchIntoArchive:
    def test_new_cluster_from_single_sub(self) -> None:
        archive = Archive()
        archive.next_cluster_id = 0  # pin base so id assertions are config-independent
        vecs = _unit(np.array([1.0, 0.0, 0.0]))[None, :].repeat(5, axis=0)
        sub = _make_sub("b0_1_0", vecs=vecs)
        node_meta = [ClusterNode(kind="sub", id="b0_1_0")]
        stats, group_info, n2c = merge_batch_into_archive(
            archive,
            batch_idx=0,
            cluster_members_idx=[{0}],
            node_meta=node_meta,
            new_sub_tracklets={"b0_1_0": sub},
            new_cannot_merge_sub_pairs=[],
            blend_strategy="count",
            ema_alpha=0.3,
        )
        assert stats.n_new_clusters == 1
        assert stats.n_updated == 0
        assert stats.n_clusters_total == 1
        assert group_info[0].status == "new"
        assert group_info[0].surviving == 0
        assert archive.next_cluster_id == 1
        assert 0 in archive.clusters
        assert archive.clusters[0].n_embs == 5
        np.testing.assert_allclose(archive.clusters[0].rep, np.array([1.0, 0.0, 0.0]), atol=1e-5)

    def test_updated_cluster_blends_count_weighted(self) -> None:
        archive = Archive()
        archive.clusters[0] = _identity_cluster(0, np.array([1.0, 0.0]), n_embs=20)
        archive.next_cluster_id = 1
        # New sub 90% overlapping with old rep but offset a bit
        new_dir = _unit(np.array([0.95, 0.31]))
        vecs = new_dir[None, :].repeat(10, axis=0)
        sub = _make_sub("b1_5_0", vecs=vecs, object_id=5)
        node_meta = [
            ClusterNode(kind="cluster", id=0),
            ClusterNode(kind="sub", id="b1_5_0"),
        ]
        stats, group_info, _ = merge_batch_into_archive(
            archive,
            batch_idx=1,
            cluster_members_idx=[{0, 1}],
            node_meta=node_meta,
            new_sub_tracklets={"b1_5_0": sub},
            new_cannot_merge_sub_pairs=[],
            blend_strategy="count",
            ema_alpha=0.3,
        )
        assert stats.n_updated == 1
        assert stats.n_clusters_total == 1
        assert archive.clusters[0].n_embs == 30
        assert archive.clusters[0].last_updated_batch == 1
        # rep should be L2-normalized
        np.testing.assert_allclose(np.linalg.norm(archive.clusters[0].rep), 1.0, atol=1e-6)

    def test_merge_two_old_clusters_retires_larger_id(self) -> None:
        archive = Archive()
        archive.clusters[0] = _identity_cluster(0, np.array([1.0, 0.0]), n_embs=10)
        archive.clusters[5] = _identity_cluster(5, np.array([1.0, 0.01]), n_embs=8)
        archive.next_cluster_id = 6
        # New sub bridges them
        vecs = _unit(np.array([1.0, 0.005]))[None, :].repeat(3, axis=0)
        sub = _make_sub("b2_3_0", vecs=vecs)
        node_meta = [
            ClusterNode(kind="cluster", id=0),
            ClusterNode(kind="cluster", id=5),
            ClusterNode(kind="sub", id="b2_3_0"),
        ]
        stats, group_info, _ = merge_batch_into_archive(
            archive,
            batch_idx=2,
            cluster_members_idx=[{0, 1, 2}],
            node_meta=node_meta,
            new_sub_tracklets={"b2_3_0": sub},
            new_cannot_merge_sub_pairs=[],
            blend_strategy="count",
            ema_alpha=0.3,
        )
        assert stats.n_retired == 1
        assert stats.n_clusters_total == 1
        assert 0 in archive.clusters
        assert 5 not in archive.clusters  # retired
        assert archive.clusters[0].n_embs == 21  # 10 + 8 + 3

    def test_unchanged_cluster_preserved_verbatim(self) -> None:
        archive = Archive()
        archive.clusters[0] = _identity_cluster(0, np.array([1.0, 0.0]), n_embs=10)
        archive.next_cluster_id = 1
        original = archive.clusters[0]
        node_meta = [ClusterNode(kind="cluster", id=0)]
        stats, _, _ = merge_batch_into_archive(
            archive,
            batch_idx=1,
            cluster_members_idx=[{0}],
            node_meta=node_meta,
            new_sub_tracklets={},
            new_cannot_merge_sub_pairs=[],
            blend_strategy="count",
            ema_alpha=0.3,
        )
        assert stats.n_unchanged == 1
        assert archive.clusters[0].n_embs == original.n_embs
        assert archive.clusters[0].last_updated_batch == 0  # unchanged
        np.testing.assert_array_equal(archive.clusters[0].rep, original.rep)

    def test_members_history_accumulates(self) -> None:
        archive = Archive()
        archive.clusters[0] = _identity_cluster(0, np.array([1.0, 0.0]), n_embs=10)
        archive.next_cluster_id = 1

        vecs = _unit(np.array([1.0, 0.01]))[None, :].repeat(3, axis=0)
        sub = _make_sub("b1_7_0", vecs=vecs, object_id=7)
        node_meta = [ClusterNode(kind="cluster", id=0), ClusterNode(kind="sub", id="b1_7_0")]
        merge_batch_into_archive(
            archive,
            batch_idx=1,
            cluster_members_idx=[{0, 1}],
            node_meta=node_meta,
            new_sub_tracklets={"b1_7_0": sub},
            new_cannot_merge_sub_pairs=[],
            blend_strategy="count",
            ema_alpha=0.3,
        )
        history = archive.clusters[0].members_history
        # Old history entry + new sub = 2
        assert len(history) == 2
        assert any(e.sub_name == "b1_7_0" for e in history)
        assert any(e.sub_name == "old_0" for e in history)

    def test_cannot_merge_remap_through_retirement(self) -> None:
        archive = Archive()
        archive.clusters[0] = _identity_cluster(0, np.array([1.0, 0.0]), n_embs=5)
        archive.clusters[2] = _identity_cluster(2, np.array([0.9, 0.43]), n_embs=5)
        archive.clusters[3] = _identity_cluster(3, np.array([0.5, 0.87]), n_embs=5)
        archive.cannot_merge_clusters = {frozenset({2, 3})}
        archive.next_cluster_id = 4

        # Merge 0 and 2 via a new sub; cluster 3 is left alone
        vecs = _unit(np.array([0.95, 0.1]))[None, :].repeat(3, axis=0)
        sub = _make_sub("b1_9_0", vecs=vecs)
        node_meta = [
            ClusterNode(kind="cluster", id=0),
            ClusterNode(kind="cluster", id=2),
            ClusterNode(kind="cluster", id=3),
            ClusterNode(kind="sub", id="b1_9_0"),
        ]
        merge_batch_into_archive(
            archive,
            batch_idx=1,
            cluster_members_idx=[{0, 1, 3}, {2}],
            node_meta=node_meta,
            new_sub_tracklets={"b1_9_0": sub},
            new_cannot_merge_sub_pairs=[],
            blend_strategy="count",
            ema_alpha=0.3,
        )
        # After retire, 2 -> 0. cannot_merge should now be {0, 3}
        assert frozenset({0, 3}) in archive.cannot_merge_clusters
        assert frozenset({2, 3}) not in archive.cannot_merge_clusters

    def test_promotes_new_sub_sub_conflict_to_cluster(self) -> None:
        archive = Archive()
        archive.next_cluster_id = 0
        vecs_a = _unit(np.array([1.0, 0.0]))[None, :].repeat(3, axis=0)
        vecs_b = _unit(np.array([0.0, 1.0]))[None, :].repeat(3, axis=0)
        sub_a = _make_sub("b0_1_0", vecs=vecs_a, object_id=1)
        sub_b = _make_sub("b0_2_0", vecs=vecs_b, object_id=2)
        node_meta = [
            ClusterNode(kind="sub", id="b0_1_0"),
            ClusterNode(kind="sub", id="b0_2_0"),
        ]
        # Two separate clusters (distinct groups)
        merge_batch_into_archive(
            archive,
            batch_idx=0,
            cluster_members_idx=[{0}, {1}],
            node_meta=node_meta,
            new_sub_tracklets={"b0_1_0": sub_a, "b0_2_0": sub_b},
            new_cannot_merge_sub_pairs=[frozenset({"b0_1_0", "b0_2_0"})],
            blend_strategy="count",
            ema_alpha=0.3,
        )
        # Both clusters exist and cannot-merge was promoted to cluster level
        assert len(archive.clusters) == 2
        cluster_ids = set(archive.clusters)
        assert archive.cannot_merge_clusters == {frozenset(cluster_ids)}

    def test_allocated_ids_monotonically_increase(self) -> None:
        archive = Archive()
        archive.next_cluster_id = 0  # pin base so id assertions are config-independent
        node_meta = [ClusterNode(kind="sub", id="s0"), ClusterNode(kind="sub", id="s1")]
        vecs = _unit(np.array([1.0, 0.0]))[None, :].repeat(3, axis=0)
        sub_a = _make_sub("s0", vecs=vecs, object_id=1)
        sub_b = _make_sub("s1", vecs=_unit(np.array([0.0, 1.0]))[None, :].repeat(3, axis=0), object_id=2)
        merge_batch_into_archive(
            archive,
            batch_idx=0,
            cluster_members_idx=[{0}, {1}],
            node_meta=node_meta,
            new_sub_tracklets={"s0": sub_a, "s1": sub_b},
            new_cannot_merge_sub_pairs=[],
            blend_strategy="count",
            ema_alpha=0.3,
        )
        assert set(archive.clusters) == {0, 1}
        assert archive.next_cluster_id == 2

    def test_ema_strategy_produces_valid_unit_rep(self) -> None:
        archive = Archive()
        archive.clusters[0] = _identity_cluster(0, np.array([1.0, 0.0]), n_embs=20)
        archive.next_cluster_id = 1
        vecs = _unit(np.array([0.0, 1.0]))[None, :].repeat(4, axis=0)
        sub = _make_sub("b1_1_0", vecs=vecs)
        node_meta = [
            ClusterNode(kind="cluster", id=0),
            ClusterNode(kind="sub", id="b1_1_0"),
        ]
        merge_batch_into_archive(
            archive,
            batch_idx=1,
            cluster_members_idx=[{0, 1}],
            node_meta=node_meta,
            new_sub_tracklets={"b1_1_0": sub},
            new_cannot_merge_sub_pairs=[],
            blend_strategy="ema",
            ema_alpha=0.3,
        )
        np.testing.assert_allclose(np.linalg.norm(archive.clusters[0].rep), 1.0, atol=1e-6)

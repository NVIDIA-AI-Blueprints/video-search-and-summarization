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

"""Tests for :mod:`cluster_manager.affinity`."""

from __future__ import annotations

import numpy as np
import pytest

from cluster_manager.affinity import build_reciprocal_affinity_matrix


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


class TestBuildReciprocalAffinityMatrix:
    def test_diagonal_is_one(self, rng: np.random.Generator) -> None:
        features = [_unit(rng.standard_normal(8)) for _ in range(10)]
        affinity, _, _ = build_reciprocal_affinity_matrix(features, [], lambda_blend=0.5, cos_neighbor_threshold=0.0)
        np.testing.assert_allclose(np.diag(affinity), 1.0)

    def test_output_shapes(self, rng: np.random.Generator) -> None:
        features = [_unit(rng.standard_normal(8)) for _ in range(6)]
        affinity, pre, cos_sim = build_reciprocal_affinity_matrix(
            features, [], lambda_blend=0.5, cos_neighbor_threshold=0.0
        )
        assert affinity.shape == (6, 6)
        assert pre.shape == (6, 6)
        assert cos_sim.shape == (6, 6)

    def test_symmetry(self, rng: np.random.Generator) -> None:
        features = [_unit(rng.standard_normal(8)) for _ in range(6)]
        affinity, _, cos_sim = build_reciprocal_affinity_matrix(
            features, [], lambda_blend=0.5, cos_neighbor_threshold=0.0
        )
        np.testing.assert_allclose(cos_sim, cos_sim.T, atol=1e-6)
        # Affinity is almost symmetric (k-reciprocal can produce tiny asymm due to
        # expansion; within tolerance for most cases)
        np.testing.assert_allclose(affinity, affinity.T, atol=1e-5)

    def test_cannot_merge_forces_zero(self, rng: np.random.Generator) -> None:
        features = [_unit(rng.standard_normal(8)) for _ in range(5)]
        cm = [frozenset({0, 3})]
        affinity, pre, _ = build_reciprocal_affinity_matrix(features, cm, lambda_blend=0.5, cos_neighbor_threshold=0.0)
        assert affinity[0, 3] == 0.0
        assert affinity[3, 0] == 0.0
        # pre_constraint preserves symmetric non-zero affinity for this pair.
        assert pre[0, 3] != 0.0 and pre[3, 0] != 0.0
        np.testing.assert_allclose(pre[0, 3], pre[3, 0], atol=1e-6)

    def test_cannot_merge_malformed_pair_skipped(self, rng: np.random.Generator, caplog) -> None:
        # Malformed pairs (singleton, triple) must not crash; they should
        # be skipped with a warning and the remaining matrix stays valid.
        features = [_unit(rng.standard_normal(8)) for _ in range(5)]
        cm = [frozenset({0}), frozenset({1, 2, 3}), frozenset({0, 4})]
        with caplog.at_level("WARNING", logger="cluster_manager.affinity"):
            affinity, _, _ = build_reciprocal_affinity_matrix(
                features, cm, lambda_blend=0.5, cos_neighbor_threshold=0.0
            )
        # Only the well-formed pair should zero out.
        assert affinity[0, 4] == 0.0
        assert affinity[4, 0] == 0.0
        # Matrix is otherwise valid (diagonal still 1, shape still (5, 5)).
        assert affinity.shape == (5, 5)
        assert affinity[0, 0] == pytest.approx(1.0)
        # Warnings were emitted for both malformed entries.
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 2

    def test_two_identical_nodes_get_max_affinity(self, rng: np.random.Generator) -> None:
        v = _unit(rng.standard_normal(8))
        # Two copies + some other random nodes
        features = [v, v] + [_unit(rng.standard_normal(8)) for _ in range(5)]
        affinity, _, cos_sim = build_reciprocal_affinity_matrix(
            features, [], lambda_blend=0.5, cos_neighbor_threshold=0.0
        )
        assert cos_sim[0, 1] == pytest.approx(1.0, abs=1e-6)
        assert affinity[0, 1] > 0.95

    def test_orthogonal_nodes_below_threshold_suppressed(self) -> None:
        # Fully orthogonal basis vectors → cosine sim = 0 everywhere off-diagonal
        features = [np.eye(6)[i].astype(np.float32) for i in range(6)]
        # High cosine neighbor threshold means no pair qualifies
        affinity, _, _ = build_reciprocal_affinity_matrix(features, [], lambda_blend=0.5, cos_neighbor_threshold=0.5)
        off_diag = affinity - np.diag(np.diag(affinity))
        # All off-diagonals should be very low (jaccard forced to 1 + cos_dist=1 blend)
        assert (off_diag < 0.1).all()

    def test_lambda_zero_means_pure_jaccard(self, rng: np.random.Generator) -> None:
        features = [_unit(rng.standard_normal(8)) for _ in range(8)]
        affinity_pure_j, _, _ = build_reciprocal_affinity_matrix(
            features, [], lambda_blend=0.0, cos_neighbor_threshold=0.0
        )
        # All off-diag entries are in [0, 1]
        assert (affinity_pure_j >= 0.0).all()
        assert (affinity_pure_j <= 1.0).all()

    def test_lambda_one_means_pure_cosine(self, rng: np.random.Generator) -> None:
        features = [_unit(rng.standard_normal(8)) for _ in range(8)]
        affinity, _, cos_sim = build_reciprocal_affinity_matrix(
            features, [], lambda_blend=1.0, cos_neighbor_threshold=0.0
        )
        # final_dist = lambda * original_dist = 1 - cos_sim
        # affinity = 1 - final_dist = cos_sim (clipped to [0, 1] at diagonal)
        np.testing.assert_allclose(affinity, cos_sim, atol=1e-5)

    def test_cos_sim_in_valid_range(self, rng: np.random.Generator) -> None:
        features = [_unit(rng.standard_normal(8)) for _ in range(6)]
        _, _, cos_sim = build_reciprocal_affinity_matrix(features, [], lambda_blend=0.5, cos_neighbor_threshold=0.0)
        assert (cos_sim >= -1.0).all()
        assert (cos_sim <= 1.0).all()

    def test_single_node(self) -> None:
        features = [np.array([1.0, 0.0, 0.0], dtype=np.float32)]
        affinity, _, cos_sim = build_reciprocal_affinity_matrix(
            features, [], lambda_blend=0.5, cos_neighbor_threshold=0.0
        )
        assert affinity.shape == (1, 1)
        assert affinity[0, 0] == pytest.approx(1.0)
        assert cos_sim[0, 0] == pytest.approx(1.0)

    def test_jaccard_tiling_matches_untiled(self, rng: np.random.Generator, monkeypatch: pytest.MonkeyPatch) -> None:
        """Double-blocked Jaccard must match the output of a single-block
        computation for the same input. Guards against off-by-one bugs in
        the col_start loop, mirroring, or the symmetric-tile shortcut.
        """
        from cluster_manager import affinity as aff_mod

        n = 40
        features = [_unit(rng.standard_normal(16)) for _ in range(n)]
        kwargs = dict(lambda_blend=0.35, cos_neighbor_threshold=0.0)

        # Large blocks: everything fits in one row-tile AND one col-tile,
        # so the inner loop runs once. This is the "effectively un-tiled"
        # reference path.
        monkeypatch.setattr(aff_mod, "_JACCARD_BLOCK_SIZE", 1000)
        monkeypatch.setattr(aff_mod, "_JACCARD_COL_BLOCK_SIZE", 1000)
        ref_affinity, ref_pre, ref_cos = aff_mod.build_reciprocal_affinity_matrix(features, [], **kwargs)

        # Small blocks: 7 and 11 don't evenly divide 40, which exercises
        # every boundary case (trailing partial tiles on both axes).
        monkeypatch.setattr(aff_mod, "_JACCARD_BLOCK_SIZE", 7)
        monkeypatch.setattr(aff_mod, "_JACCARD_COL_BLOCK_SIZE", 11)
        tiled_affinity, tiled_pre, tiled_cos = aff_mod.build_reciprocal_affinity_matrix(features, [], **kwargs)

        np.testing.assert_allclose(tiled_affinity, ref_affinity, atol=1e-6)
        np.testing.assert_allclose(tiled_pre, ref_pre, atol=1e-6)
        np.testing.assert_allclose(tiled_cos, ref_cos, atol=1e-6)
        # Symmetry preserved by the upper-triangle + mirror strategy.
        np.testing.assert_allclose(tiled_affinity, tiled_affinity.T, atol=1e-6)

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

"""Moment propagation through compression and recompression.

Compression deletes the raw vectors it summarizes, so the per-row
``(weight, mean)`` pairs are the only remaining description of what a
representative stands for. These tests pin the invariant that makes them
trustworthy: merging every representative's summary must reproduce the moments
of all the raw vectors that went in, no matter how many recompression cycles
have passed since.
"""

from __future__ import annotations

from argparse import Namespace
from typing import Dict, List

import numpy as np
import pytest

from cluster_sampling.sample_and_extract_patches import (
    MAX_UNIT_SQUARED_L2,
    _empty_sampling_result,
    _gated_representative_groups,
    _normalize_vectors,
    process_single_object,
    recompress_single_object,
    remove_outliers,
)
from cluster_stats.moments import combine_cluster_moments, moments_from_vectors

DIM = 64
ARGS = Namespace(outlier_threshold=3.0, cosine_threshold=0.9995, max_samples=5, seed=100)


def _embeddings(rng: np.random.Generator, object_id: str, n: int, entry_id_base: int, n_modes: int = 3) -> Dict:
    """Multi-modal unit-norm embeddings, shaped like one object's track."""
    modes = rng.normal(size=(n_modes, DIM))
    modes /= np.linalg.norm(modes, axis=1, keepdims=True)
    vectors = modes[rng.integers(0, n_modes, size=n)]
    vectors = vectors + rng.normal(scale=0.05, size=(n, DIM))
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return {
        "entry_ids": np.arange(entry_id_base, entry_id_base + n),
        "object_ids": np.array([object_id] * n),
        "sensor_ids": np.array([f"cam{i % 2}" for i in range(n)]),
        "frame_ids": np.arange(1000, 1000 + n),
        "vectors": vectors.astype(np.float32),
    }


def _compress(object_id: str, embeddings: Dict) -> List[dict]:
    """Run one compression pass and shape the result like a compressed row."""
    (entry_ids, obj_ids, sensor_ids, frame_ids, weights, vectors, means, _outliers, _stats) = process_single_object(
        object_id, embeddings, ARGS
    )
    return [
        {
            "entry_id": int(entry_ids[i]),
            "object_id": str(obj_ids[i]),
            "sensor_id": str(sensor_ids[i]),
            "frame_id": int(frame_ids[i]),
            "weight": int(weights[i]),
            "vector": vectors[i].tolist(),
            "mean": means[i].tolist(),
        }
        for i in range(len(entry_ids))
    ]


def _items_from_directions(directions: np.ndarray) -> List[dict]:
    """One compressed row per direction, each standing for a single vector.

    Random directions in DIM dimensions are near-orthogonal, so consecutive
    rows sit ~2.0 apart in squared L2 -- far outside any sane merge gate.
    """
    return [
        {
            "entry_id": i,
            "object_id": "42",
            "sensor_id": f"cam{i % 2}",
            "frame_id": 1000 + i,
            "weight": i + 1,
            "vector": directions[i].tolist(),
            "mean": directions[i].tolist(),
        }
        for i in range(len(directions))
    ]


def _unit_directions(rng: np.random.Generator, n: int) -> np.ndarray:
    directions = rng.normal(size=(n, DIM))
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)
    return directions.astype(np.float32)


def _summarized_vectors(embeddings: Dict) -> np.ndarray:
    """The vectors a compression pass actually summarizes: survivors of
    outlier removal, L2-normalized."""
    filtered, _ = remove_outliers(embeddings["vectors"], threshold_std=ARGS.outlier_threshold)
    return _normalize_vectors(filtered)


def _assert_summarizes(items: List[dict], vectors: np.ndarray) -> None:
    count, mean = combine_cluster_moments(
        [it["weight"] for it in items],
        np.array([it["mean"] for it in items]),
    )
    expected_count, expected_mean = moments_from_vectors(vectors)
    assert count == expected_count
    # float32 is the storage precision of the mean in Milvus.
    np.testing.assert_allclose(mean, expected_mean, atol=1e-5)
    # The spread the estimator reads off the merged mean has to match the raw
    # vectors' total variance, which is the whole reason only the mean is kept.
    assert 1.0 - float(np.dot(mean, mean)) == pytest.approx(
        float(np.asarray(vectors, dtype=np.float64).var(axis=0).sum()), abs=1e-5
    )


def _squared_distances(vectors: np.ndarray) -> np.ndarray:
    v = np.asarray(vectors, dtype=np.float64)
    return ((v[:, None, :] - v[None, :, :]) ** 2).sum(-1)


def _ring(angles_deg: np.ndarray) -> np.ndarray:
    """Unit vectors at given angles in one plane of a DIM-dimensional space.

    Squared L2 distance between two of them is ``2 - 2cos(delta)``, so the
    geometry a merge gate sees is exactly controllable from the angles.
    """
    vectors = np.zeros((len(angles_deg), DIM), dtype=np.float32)
    radians = np.deg2rad(angles_deg)
    vectors[:, 0] = np.cos(radians)
    vectors[:, 1] = np.sin(radians)
    return vectors


class TestGatedRepresentativeGroups:
    GATE = 0.4

    def test_proximity_to_the_seed_alone_is_not_enough(self) -> None:
        """A representative near the seed but far from the rest of the group
        stays out: 0 and 2 are 1.12 apart while each is within 0.3 of seed 1,
        so a seed-only test would have fused them."""
        vectors = _ring(np.array([0.0, 32.0, 64.0]))
        weights = np.array([1, 1, 1])

        retained, assignments = _gated_representative_groups(vectors, np.array([1]), weights, self.GATE)

        distances = _squared_distances(vectors)
        assert distances[0, 1] < self.GATE < distances[0, 2]
        assert assignments[0] != assignments[2]
        assert len(retained) == 2

    def test_group_mean_pairwise_distance_within_the_gate(self) -> None:
        """Every join keeps the candidate's mean distance to the members it
        joins inside the gate, and those pair sets partition the group, so the
        group's overall mean pairwise distance is bounded by the gate too."""
        rng = np.random.default_rng(14)
        modes = _unit_directions(rng, 5)
        vectors = modes[rng.integers(0, 5, size=40)] + rng.normal(scale=0.08, size=(40, DIM))
        vectors = _normalize_vectors(vectors)
        weights = rng.integers(1, 50, size=40)

        retained, assignments = _gated_representative_groups(vectors, np.arange(3), weights, self.GATE)

        distances = _squared_distances(vectors)
        for group in range(len(retained)):
            members = np.flatnonzero(assignments == group)
            assert members.size > 0
            pairs = distances[np.ix_(members, members)][np.triu_indices(members.size, 1)]
            if pairs.size:
                assert pairs.mean() <= self.GATE

    def test_gradual_drift_consolidates(self) -> None:
        """One identity's appearance drift must still collapse: a sweep whose
        ends are 1.9 apart merges, since each step is close to the bulk."""
        vectors = _ring(np.linspace(0.0, 90.0, 10))
        weights = np.ones(10, dtype=int)

        retained, _ = _gated_representative_groups(vectors, np.array([5]), weights, self.GATE)

        assert _squared_distances(vectors)[0, -1] > 4 * self.GATE
        assert len(retained) < 4

    def test_every_input_lands_in_exactly_one_group(self) -> None:
        rng = np.random.default_rng(15)
        vectors = _normalize_vectors(_unit_directions(rng, 12))
        weights = np.arange(1, 13)

        retained, assignments = _gated_representative_groups(vectors, np.array([0, 5]), weights, self.GATE)

        assert len(assignments) == len(vectors)
        assert set(assignments) == set(range(len(retained)))
        # Seeds keep their own rows; near-orthogonal inputs can share none.
        assert set(retained) >= {0, 5}

    def test_widest_gate_keeps_only_the_seeds(self) -> None:
        """4.0 is the largest squared L2 between unit vectors, so every merge
        is admissible and the pass degenerates to nearest-seed assignment."""
        rng = np.random.default_rng(16)
        vectors = _normalize_vectors(_unit_directions(rng, 12))
        weights = np.arange(1, 13)

        retained, assignments = _gated_representative_groups(vectors, np.array([0, 5]), weights, 4.0)

        assert list(retained) == [0, 5]
        assert set(assignments) == {0, 1}

    def test_off_switch_shortcut_matches_the_general_path(self) -> None:
        """The MAX_UNIT_SQUARED_L2 shortcut skips the distance matrix; it must
        return what the general path returns for a threshold just below it."""
        rng = np.random.default_rng(19)
        vectors = _normalize_vectors(_unit_directions(rng, 15))
        weights = rng.integers(1, 100, size=15)
        seeds = np.array([1, 4, 9])

        shortcut = _gated_representative_groups(vectors, seeds, weights, MAX_UNIT_SQUARED_L2)
        general = _gated_representative_groups(vectors, seeds, weights, MAX_UNIT_SQUARED_L2 - 0.1)

        np.testing.assert_array_equal(shortcut[0], general[0])
        np.testing.assert_array_equal(shortcut[1], general[1])

    def test_zero_gate_merges_only_exact_duplicates(self) -> None:
        """The strict end of the knob: a duplicate still merges, anything
        further apart does not."""
        rng = np.random.default_rng(17)
        vectors = _normalize_vectors(_unit_directions(rng, 4))
        vectors = np.vstack([vectors, vectors[2]])
        weights = np.array([5, 4, 3, 2, 1])

        retained, assignments = _gated_representative_groups(vectors, np.arange(4), weights, 0.0)

        assert list(retained) == [0, 1, 2, 3]
        assert assignments[4] == assignments[2]


class TestProcessSingleObject:
    def test_representatives_summarize_their_inputs(self) -> None:
        rng = np.random.default_rng(3)
        embeddings = _embeddings(rng, "42", 200, entry_id_base=0)
        items = _compress("42", embeddings)
        assert items
        _assert_summarizes(items, _summarized_vectors(embeddings))

    def test_every_representative_carries_a_nonempty_cluster(self) -> None:
        rng = np.random.default_rng(4)
        items = _compress("42", _embeddings(rng, "42", 150, entry_id_base=0))
        assert all(it["weight"] > 0 for it in items)

    def test_means_are_full_length_and_no_longer_than_unit(self) -> None:
        """A mean of unit-norm vectors cannot exceed unit length, and its
        shortfall is the spread the estimator will read back out."""
        rng = np.random.default_rng(5)
        items = _compress("42", _embeddings(rng, "42", 120, entry_id_base=0))
        for it in items:
            assert len(it["mean"]) == DIM
            assert np.linalg.norm(it["mean"]) <= 1.0 + 1e-6

    def test_single_vector_object_has_zero_spread(self) -> None:
        """Its mean is the vector itself, so the mean stays unit-length and the
        row reports no spread."""
        rng = np.random.default_rng(6)
        embeddings = _embeddings(rng, "42", 1, entry_id_base=0)
        items = _compress("42", embeddings)
        assert len(items) == 1
        assert items[0]["weight"] == 1
        assert np.linalg.norm(items[0]["mean"]) == pytest.approx(1.0, abs=1e-6)

    def test_empty_result_unpacks_like_the_success_path(self) -> None:
        """Both return paths must have the same arity; callers unpack
        unconditionally."""
        stats = {"after_outlier_removal": 0, "after_cosine_prune": 0, "final_sampled": 0}
        result = _empty_sampling_result({"entry_ids": np.array([])}, stats)
        (entry_ids, obj_ids, sensor_ids, frame_ids, weights, vectors, means, outliers, out_stats) = result
        assert len(entry_ids) == 0 and len(means) == 0
        assert out_stats is stats


class TestRecompressSingleObject:
    def test_merged_representatives_still_summarize_everything(self) -> None:
        rng = np.random.default_rng(7)
        first = _embeddings(rng, "42", 200, entry_id_base=0)
        second = _embeddings(rng, "42", 180, entry_id_base=10_000)
        items = _compress("42", second) + _compress("42", first)

        merged = recompress_single_object(items, ARGS)

        assert 0 < len(merged) <= len(items)
        _assert_summarizes(
            merged,
            np.vstack([_summarized_vectors(first), _summarized_vectors(second)]),
        )

    def test_repeated_cycles_do_not_drift(self) -> None:
        rng = np.random.default_rng(8)
        batches = [_embeddings(rng, "42", 90, entry_id_base=i * 1000) for i in range(6)]
        state = _compress("42", batches[0])
        for batch in batches[1:]:
            state = recompress_single_object(_compress("42", batch) + state, ARGS)

        _assert_summarizes(state, np.vstack([_summarized_vectors(b) for b in batches]))

    def test_duplicate_representatives_with_the_gate_off(self) -> None:
        """With the gate off, assignment is pure nearest-representative, so a
        duplicate's mass lands entirely on the tie-break winner. The loser must
        be dropped rather than persisted at weight 0."""
        rng = np.random.default_rng(18)
        items = _compress("42", _embeddings(rng, "42", 120, entry_id_base=0))
        duplicates = [{**it, "entry_id": it["entry_id"] + 500_000} for it in items]
        total_weight = sum(it["weight"] for it in items + duplicates)
        ungated = Namespace(**{**vars(ARGS), "merge_max_distance": 4.0})

        merged = recompress_single_object(items + duplicates, ungated)

        assert all(it["weight"] > 0 for it in merged)
        assert sum(it["weight"] for it in merged) == total_weight

    def test_duplicate_representatives_never_yield_zero_weight(self) -> None:
        """Representatives holding identical vectors each keep their own mass
        rather than one being absorbed and persisted with weight 0, which
        would fail the next weighted k-medoids pass."""
        rng = np.random.default_rng(9)
        items = _compress("42", _embeddings(rng, "42", 150, entry_id_base=0))
        duplicates = [{**it, "entry_id": it["entry_id"] + 500_000} for it in items]
        total_weight = sum(it["weight"] for it in items + duplicates)

        merged = recompress_single_object(items + duplicates, ARGS)

        assert all(it["weight"] > 0 for it in merged)
        assert sum(it["weight"] for it in merged) == total_weight

    def test_distant_representatives_outlive_the_sample_budget(self) -> None:
        """A representative that no sample can describe must survive rather
        than be folded into an unrelated one, even when there are more modes
        than the budget allows samples."""
        rng = np.random.default_rng(11)
        items = _items_from_directions(_unit_directions(rng, 8))
        tight_budget = Namespace(**{**vars(ARGS), "max_samples": 2})

        merged = recompress_single_object(items, tight_budget)

        assert len(merged) == len(items)
        assert sorted(it["entry_id"] for it in merged) == sorted(it["entry_id"] for it in items)
        assert all(it["weight"] == 1 + it["entry_id"] for it in merged)

    def test_nearby_representatives_still_merge(self) -> None:
        """The gate must not block the compression it is guarding: rows within
        the threshold of a sample are still absorbed by it."""
        rng = np.random.default_rng(12)
        axis = _unit_directions(rng, 1)[0]
        cluster = axis + rng.normal(scale=0.01, size=(8, DIM))
        cluster /= np.linalg.norm(cluster, axis=1, keepdims=True)
        items = _items_from_directions(cluster.astype(np.float32))
        tight_budget = Namespace(**{**vars(ARGS), "max_samples": 2})

        merged = recompress_single_object(items, tight_budget)

        assert len(merged) < len(items)
        assert sum(it["weight"] for it in merged) == sum(it["weight"] for it in items)

    def test_widest_gate_restores_unconditional_merging(self) -> None:
        """merge_max_distance=4.0 admits every merge, so the sample budget
        binds again and distant representatives are folded together."""
        rng = np.random.default_rng(13)
        items = _items_from_directions(_unit_directions(rng, 8))
        ungated = Namespace(**{**vars(ARGS), "max_samples": 2, "merge_max_distance": 4.0})

        merged = recompress_single_object(items, ungated)

        assert len(merged) < len(items)
        assert sum(it["weight"] for it in merged) == sum(it["weight"] for it in items)

    def test_requires_moments_on_every_input_row(self) -> None:
        rng = np.random.default_rng(10)
        items = _compress("42", _embeddings(rng, "42", 100, entry_id_base=0))
        del items[0]["mean"]
        with pytest.raises(KeyError):
            recompress_single_object(items, ARGS)

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

"""Shared test fixtures for cluster_manager tests."""

from __future__ import annotations

from typing import List

import numpy as np
import pytest

from cluster_manager.types import EmbeddingRecord


@pytest.fixture(autouse=True)
def _disable_cluster_rep_offset(monkeypatch):
    """Validate the core cluster_id -> object_id mapping independent of the
    deployment-only CLUSTER_REP_DEBUG_OFFSET (which shifts cluster-rep ids into
    a high range purely for log/search readability). Pin it to 0 so tests
    assert the un-shifted representative ids."""
    monkeypatch.setattr("cluster_manager.compression_labeling.CLUSTER_REP_DEBUG_OFFSET", 0)


_UNIT_NORM_FLOOR = 1e-12


def _unit(v: np.ndarray) -> np.ndarray:
    """L2-normalize a vector.

    Raises :class:`ValueError` when ``||v|| <= 1e-12`` so tests can't
    silently receive a non-unit vector from a degenerate input. Callers
    that genuinely need to handle zero-norm inputs must do so explicitly.
    """
    n = np.linalg.norm(v)
    if n <= _UNIT_NORM_FLOOR:
        raise ValueError(
            f"_unit: vector norm {n!r} is at/below threshold {_UNIT_NORM_FLOOR}; "
            "cannot produce a unit vector from a degenerate input"
        )
    return v / n


@pytest.fixture
def rng() -> np.random.Generator:
    """Deterministic RNG for reproducibility across tests."""
    return np.random.default_rng(seed=20260420)


@pytest.fixture
def make_embedding():
    """Factory for :class:`EmbeddingRecord`.

    Usage::
        rec = make_embedding(entry_id=1, object_id=10, sensor_id='cam1',
                             frame_id=100, vector=np.array([...]))
    """

    def _make(
        *,
        entry_id: int,
        object_id: int,
        sensor_id: str,
        frame_id: int,
        vector: np.ndarray,
    ) -> EmbeddingRecord:
        return EmbeddingRecord(
            entry_id=entry_id,
            object_id=object_id,
            sensor_id=sensor_id,
            frame_id=frame_id,
            vector=_unit(vector.astype(np.float32)),
        )

    return _make


@pytest.fixture
def similar_vectors(rng: np.random.Generator):
    """Factory generating `n` vectors clustered near a common unit direction.

    ``spread`` controls how wide the cluster is; smaller = more similar.
    """

    def _make(n: int, dim: int = 8, spread: float = 0.02) -> List[np.ndarray]:
        axis = _unit(rng.standard_normal(dim))
        out: List[np.ndarray] = []
        for _ in range(n):
            noise = rng.standard_normal(dim) * spread
            out.append(_unit(axis + noise))
        return out

    return _make

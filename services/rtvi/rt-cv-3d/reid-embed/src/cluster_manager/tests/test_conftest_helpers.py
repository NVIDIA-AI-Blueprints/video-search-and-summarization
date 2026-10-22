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

"""Tests for the test scaffolding in :mod:`cluster_manager.tests.conftest`.

Verifies the invariants of helper functions imported and relied upon by
every other test in the suite. If one of these breaks, every dependent
test file will report a confusing downstream failure instead.
"""

from __future__ import annotations

import numpy as np
import pytest

from cluster_manager.tests.conftest import _unit


class TestUnit:
    def test_basic_unit_vector(self) -> None:
        v = np.array([3.0, 4.0, 0.0], dtype=np.float32)
        out = _unit(v)
        np.testing.assert_allclose(np.linalg.norm(out), 1.0, atol=1e-6)

    def test_preserves_direction(self) -> None:
        v = np.array([2.0, 0.0], dtype=np.float32)
        out = _unit(v)
        np.testing.assert_allclose(out, np.array([1.0, 0.0]), atol=1e-6)

    def test_zero_vector_raises(self) -> None:
        v = np.zeros(4, dtype=np.float32)
        with pytest.raises(ValueError, match="norm"):
            _unit(v)

    def test_near_zero_vector_raises(self) -> None:
        # Below the 1e-12 floor the result would be numerically meaningless;
        # raise instead of silently returning a near-infinity-amplified vector.
        v = np.array([1e-20, 0.0, 0.0], dtype=np.float32)
        with pytest.raises(ValueError, match="norm"):
            _unit(v)

    def test_threshold_boundary_raises(self) -> None:
        # Exactly at the floor: still raise (``<=`` comparison in _unit).
        v = np.array([1e-12, 0.0], dtype=np.float32)
        with pytest.raises(ValueError, match="norm"):
            _unit(v)

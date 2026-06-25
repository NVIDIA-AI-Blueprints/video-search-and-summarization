# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Tests for ``core.post_processing.bev_boundary.is_inside_bev_boundary``.

Pins the rectangular ``[x_min, x_max, y_min, y_max]`` BEV-region
inclusion check used by warehouse post-processing to drop detections
outside the floor plan. Coverage targets both branches: ``None`` =>
always inside, and the strict ``<`` boundary semantics (boxes exactly
on the edge are *out*).
"""

import numpy as np
import pytest

from spatialai_data_utils.core.post_processing.bev_boundary import (
    is_inside_bev_boundary,
)


BOUNDS = [-50.0, 50.0, -30.0, 30.0]  # x in (-50, 50), y in (-30, 30)


def test_no_boundary_always_inside():
    assert is_inside_bev_boundary([1e9, -1e9, 0.0]) is True
    assert is_inside_bev_boundary([0.0, 0.0, 0.0], None) is True


def test_inside_returns_true():
    assert is_inside_bev_boundary([0.0, 0.0, 1.5], BOUNDS) is True
    assert is_inside_bev_boundary([49.9, 29.9, 0.0], BOUNDS) is True
    assert is_inside_bev_boundary([-49.9, -29.9, 0.0], BOUNDS) is True


@pytest.mark.parametrize("xy", [
    (60.0, 0.0),    # x past x_max
    (-60.0, 0.0),   # x past x_min
    (0.0, 40.0),    # y past y_max
    (0.0, -40.0),   # y past y_min
])
def test_outside_returns_false(xy):
    assert is_inside_bev_boundary([xy[0], xy[1], 1.5], BOUNDS) is False


@pytest.mark.parametrize("xy", [
    (-50.0, 0.0),   # exactly x_min  -> strictly-less-than test rejects
    (50.0, 0.0),    # exactly x_max
    (0.0, -30.0),   # exactly y_min
    (0.0, 30.0),    # exactly y_max
])
def test_boundary_edges_are_excluded(xy):
    """``is_inside_bev_boundary`` uses strict ``<`` on both ends, so a
    point sitting exactly on the rectangle's edge is treated as
    outside. Pin that semantics so downstream filters don't drift to
    inclusive comparisons by accident."""
    assert is_inside_bev_boundary([xy[0], xy[1], 1.5], BOUNDS) is False


def test_accepts_numpy_array_translation():
    """The translation argument is indexed by [0] / [1] only — numpy
    arrays must work the same as lists/tuples."""
    assert is_inside_bev_boundary(np.array([10.0, 20.0, 1.5]), BOUNDS) is True
    assert is_inside_bev_boundary(np.array([100.0, 200.0, 1.5]), BOUNDS) is False

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

"""Tests for :mod:`cluster_manager.geometry`."""

from __future__ import annotations

import pytest

from cluster_manager.geometry import compute_iou


class TestComputeIou:
    def test_identical_boxes_iou_is_one(self) -> None:
        box = (0.0, 0.0, 10.0, 10.0)
        assert compute_iou(box, box) == pytest.approx(1.0)

    def test_disjoint_boxes_iou_is_zero(self) -> None:
        a = (0.0, 0.0, 1.0, 1.0)
        b = (10.0, 10.0, 11.0, 11.0)
        assert compute_iou(a, b) == 0.0

    def test_touching_boxes_iou_is_zero(self) -> None:
        a = (0.0, 0.0, 1.0, 1.0)
        b = (1.0, 0.0, 2.0, 1.0)
        assert compute_iou(a, b) == 0.0

    def test_half_overlap(self) -> None:
        a = (0.0, 0.0, 2.0, 2.0)
        b = (1.0, 0.0, 3.0, 2.0)
        # intersection = 1x2 = 2, areas = 4 + 4, union = 6, IoU = 1/3
        assert compute_iou(a, b) == pytest.approx(1.0 / 3.0)

    def test_fully_contained(self) -> None:
        outer = (0.0, 0.0, 10.0, 10.0)
        inner = (2.0, 2.0, 4.0, 4.0)
        assert compute_iou(outer, inner) == pytest.approx(4.0 / 100.0)

    def test_zero_area_box_returns_zero(self) -> None:
        a = (0.0, 0.0, 0.0, 0.0)
        b = (0.0, 0.0, 1.0, 1.0)
        assert compute_iou(a, b) == 0.0

    def test_negative_dimensions_return_zero(self) -> None:
        a = (5.0, 5.0, 0.0, 0.0)  # inverted
        b = (0.0, 0.0, 1.0, 1.0)
        assert compute_iou(a, b) == 0.0

    @pytest.mark.parametrize(
        "a,b,expected",
        [
            ((0, 0, 4, 4), (0, 0, 4, 4), 1.0),
            ((0, 0, 4, 4), (2, 2, 4, 4), 0.25),
            ((0, 0, 4, 4), (4, 4, 8, 8), 0.0),
        ],
    )
    def test_parametrized(self, a: tuple, b: tuple, expected: float) -> None:
        assert compute_iou(a, b) == pytest.approx(expected)

    def test_symmetry(self) -> None:
        a = (1.0, 2.0, 5.0, 6.0)
        b = (3.0, 3.0, 7.0, 8.0)
        assert compute_iou(a, b) == compute_iou(b, a)

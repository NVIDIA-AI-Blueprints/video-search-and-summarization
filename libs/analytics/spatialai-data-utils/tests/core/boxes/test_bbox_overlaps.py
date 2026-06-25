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

"""Tests for ``core.boxes.bbox_overlaps.bbox_overlaps``.

Pins the numeric semantics that downstream NMS / matching code depends
on:

* ``mode="iou"`` is symmetric, in ``[0, 1]``.
* ``mode="iof"`` is asymmetric — divides by the area of the **first**
  argument (so for two equal boxes ``iof(a, b) == 1``, but for an
  ``a`` that is half-covered by ``b`` we expect ``iof(a, b) = 0.5``,
  ``iof(b, a) = 0.5 * |b| / |a|`` etc.).
* Empty input on either side returns a correctly shaped zero matrix.
* The internal swap-for-cache-efficiency optimisation when
  ``bboxes1.shape[0] > bboxes2.shape[0]`` does not change the output
  shape or values.
"""

import numpy as np
import pytest

from spatialai_data_utils.core.boxes.bbox_overlaps import bbox_overlaps


def test_identical_boxes_have_iou_one():
    box = np.array([[10, 20, 30, 40]], dtype=float)
    ious = bbox_overlaps(box, box, mode="iou")
    np.testing.assert_allclose(ious, [[1.0]])


def test_disjoint_boxes_have_iou_zero():
    a = np.array([[0, 0, 10, 10]], dtype=float)
    b = np.array([[100, 100, 200, 200]], dtype=float)
    ious = bbox_overlaps(a, b, mode="iou")
    np.testing.assert_allclose(ious, [[0.0]])


def test_half_overlap_iou_matches_hand_computation():
    """Two 10x10 boxes shifted by 5 along x overlap on a 5x10 strip.
    overlap=50, union=100+100-50=150  ->  IoU = 1/3."""
    a = np.array([[0, 0, 10, 10]], dtype=float)
    b = np.array([[5, 0, 15, 10]], dtype=float)
    ious = bbox_overlaps(a, b, mode="iou")
    np.testing.assert_allclose(ious, [[50.0 / 150.0]], atol=1e-6)


def test_iou_is_symmetric():
    a = np.array([[0, 0, 10, 10]], dtype=float)
    b = np.array([[5, 0, 15, 10]], dtype=float)
    np.testing.assert_allclose(
        bbox_overlaps(a, b, mode="iou"),
        bbox_overlaps(b, a, mode="iou").T,
        atol=1e-6,
    )


def test_iof_normalises_by_first_arg_area():
    """``a`` (10x10, area 100) is covered by ``b`` (20x20, area 400) on
    its full extent -> IoF(a, b) = overlap / area(a) = 100/100 = 1.0."""
    a = np.array([[0, 0, 10, 10]], dtype=float)
    b = np.array([[-5, -5, 15, 15]], dtype=float)  # covers a entirely
    iofs = bbox_overlaps(a, b, mode="iof")
    np.testing.assert_allclose(iofs, [[1.0]])


def test_iof_is_asymmetric():
    """Same overlap, different denominators -> different IoF."""
    a = np.array([[0, 0, 10, 10]], dtype=float)
    b = np.array([[-5, -5, 15, 15]], dtype=float)
    iof_ab = bbox_overlaps(a, b, mode="iof")
    iof_ba = bbox_overlaps(b, a, mode="iof")
    np.testing.assert_allclose(iof_ab, [[1.0]])
    np.testing.assert_allclose(iof_ba, [[100.0 / 400.0]], atol=1e-6)


def test_swap_path_preserves_output_shape_and_values():
    """When ``bboxes1.shape[0] > bboxes2.shape[0]`` the impl internally
    swaps then transposes — output shape must still be (N, K) and
    values must match the unswapped call."""
    bboxes1 = np.array([
        [0, 0, 10, 10],
        [5, 0, 15, 10],
        [20, 20, 30, 30],
    ], dtype=float)
    bboxes2 = np.array([[0, 0, 10, 10]], dtype=float)

    out = bbox_overlaps(bboxes1, bboxes2, mode="iou")
    assert out.shape == (3, 1)
    expected = np.array([[1.0], [50.0 / 150.0], [0.0]], dtype=np.float32)
    np.testing.assert_allclose(out, expected, atol=1e-6)


def test_empty_inputs_return_zero_shaped_matrix():
    empty = np.empty((0, 4), dtype=float)
    nonempty = np.array([[0, 0, 1, 1]], dtype=float)
    assert bbox_overlaps(empty, nonempty).shape == (0, 1)
    assert bbox_overlaps(nonempty, empty).shape == (1, 0)
    assert bbox_overlaps(empty, empty).shape == (0, 0)


def test_invalid_mode_raises():
    with pytest.raises(AssertionError):
        bbox_overlaps(
            np.array([[0, 0, 1, 1]], dtype=float),
            np.array([[0, 0, 1, 1]], dtype=float),
            mode="giou",
        )

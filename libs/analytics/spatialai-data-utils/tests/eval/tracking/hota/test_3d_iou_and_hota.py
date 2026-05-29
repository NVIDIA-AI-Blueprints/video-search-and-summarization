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

"""
Tests for 3D IoU computation and HOTA tracking evaluation.

Covers:
- eval/common/utils.py: _boxes_to_corners, _compute_iou_3d_pair, iou_3d, iou_3d_matrix
- eval/tracking/hota/metrics/hota.py: HOTA.eval_sequence, combine_sequences, _compute_final_fields
- eval/tracking/hota/datasets/_base_dataset.py: _calculate_3DBBox_ious
"""

import math

import numpy as np
import pytest
import torch
from pyquaternion import Quaternion

from spatialai_data_utils.eval.common.utils import (
    _boxes_to_corners,
    _compute_iou_3d_pair,
    iou_3d,
    iou_3d_matrix,
)
from spatialai_data_utils.eval.tracking.hota.datasets._base_dataset import _BaseDataset
from spatialai_data_utils.eval.tracking.hota.metrics.hota import HOTA
from nuscenes.eval.common.data_classes import EvalBox


# ---------------------------------------------------------------------------
# Concrete EvalBox subclass for testing (EvalBox is abstract)
# ---------------------------------------------------------------------------
class _TestBox(EvalBox):
    """Minimal concrete EvalBox for unit tests."""

    def serialize(self) -> dict:
        return {
            "sample_token": self.sample_token,
            "translation": self.translation,
            "size": self.size,
            "rotation": self.rotation,
        }

    @classmethod
    def deserialize(cls, content: dict):
        return cls(**content)


def _make_box(translation, size, rotation=(1, 0, 0, 0)):
    """Shorthand to create a _TestBox with identity rotation by default."""
    return _TestBox(
        sample_token="test",
        translation=tuple(translation),
        size=tuple(size),
        rotation=tuple(rotation),
    )


# ===================================================================
# 3D IoU — corner computation
# ===================================================================
class TestBoxesToCorners:
    def test_unit_box_at_origin(self):
        """An axis-aligned 1x1x1 box at origin should have corners at +/-0.5."""
        corners = _boxes_to_corners(
            translations=[(0, 0, 0)],
            sizes=[(1, 1, 1)],
            rotations=[(1, 0, 0, 0)],
        )
        assert corners.shape == (1, 8, 3)
        np.testing.assert_allclose(corners.min(axis=1), [[-0.5, -0.5, -0.5]], atol=1e-12)
        np.testing.assert_allclose(corners.max(axis=1), [[0.5, 0.5, 0.5]], atol=1e-12)

    def test_translated_box(self):
        """A translated box should have its corners shifted accordingly."""
        corners = _boxes_to_corners(
            translations=[(10, 20, 30)],
            sizes=[(2, 4, 6)],
            rotations=[(1, 0, 0, 0)],
        )
        np.testing.assert_allclose(corners.min(axis=1), [[9, 18, 27]], atol=1e-12)
        np.testing.assert_allclose(corners.max(axis=1), [[11, 22, 33]], atol=1e-12)

    def test_90_degree_yaw_rotation(self):
        """A 90-degree yaw rotation should swap the X and Y extents."""
        q = Quaternion(axis=[0, 0, 1], angle=math.pi / 2)
        rot = (q.w, q.x, q.y, q.z)

        # w=2, l=4 box; after 90° yaw the X extent should be ~4, Y extent ~2
        corners = _boxes_to_corners(
            translations=[(0, 0, 0)],
            sizes=[(2, 4, 1)],
            rotations=[rot],
        )
        x_range = corners[0, :, 0].max() - corners[0, :, 0].min()
        y_range = corners[0, :, 1].max() - corners[0, :, 1].min()
        np.testing.assert_allclose(x_range, 4.0, atol=1e-6)
        np.testing.assert_allclose(y_range, 2.0, atol=1e-6)

    def test_45_degree_yaw_rotation(self):
        """A 45-degree yaw rotation of a square box should produce a diamond with known extents."""
        q = Quaternion(axis=[0, 0, 1], angle=math.pi / 4)
        rot = (q.w, q.x, q.y, q.z)

        # 2x2x2 box rotated 45°: the bounding box diagonal in XY becomes 2*sqrt(2)
        corners = _boxes_to_corners(
            translations=[(0, 0, 0)],
            sizes=[(2, 2, 2)],
            rotations=[rot],
        )
        x_range = corners[0, :, 0].max() - corners[0, :, 0].min()
        y_range = corners[0, :, 1].max() - corners[0, :, 1].min()
        z_range = corners[0, :, 2].max() - corners[0, :, 2].min()
        expected_xy = 2 * math.sqrt(2)
        np.testing.assert_allclose(x_range, expected_xy, atol=1e-6)
        np.testing.assert_allclose(y_range, expected_xy, atol=1e-6)
        np.testing.assert_allclose(z_range, 2.0, atol=1e-6)

    def test_45_degree_pitch_rotation(self):
        """A 45-degree pitch rotation should tilt the box in the YZ plane."""
        q = Quaternion(axis=[1, 0, 0], angle=math.pi / 4)
        rot = (q.w, q.x, q.y, q.z)

        corners = _boxes_to_corners(
            translations=[(0, 0, 0)],
            sizes=[(2, 2, 2)],
            rotations=[rot],
        )
        x_range = corners[0, :, 0].max() - corners[0, :, 0].min()
        y_range = corners[0, :, 1].max() - corners[0, :, 1].min()
        z_range = corners[0, :, 2].max() - corners[0, :, 2].min()
        expected_yz = 2 * math.sqrt(2)
        np.testing.assert_allclose(x_range, 2.0, atol=1e-6)
        np.testing.assert_allclose(y_range, expected_yz, atol=1e-6)
        np.testing.assert_allclose(z_range, expected_yz, atol=1e-6)

    def test_multiple_boxes(self):
        """Processing multiple boxes at once should return correct shape."""
        corners = _boxes_to_corners(
            translations=[(0, 0, 0), (5, 5, 5)],
            sizes=[(1, 1, 1), (2, 2, 2)],
            rotations=[(1, 0, 0, 0), (1, 0, 0, 0)],
        )
        assert corners.shape == (2, 8, 3)


# ===================================================================
# 3D IoU — pairwise computation
# ===================================================================
class TestComputeIoU3DPair:
    def test_identical_boxes(self):
        """Two identical boxes should have IoU = 1."""
        corners = _boxes_to_corners(
            translations=[(0, 0, 0)],
            sizes=[(2, 2, 2)],
            rotations=[(1, 0, 0, 0)],
        )
        t = torch.from_numpy(corners).float()
        iou = _compute_iou_3d_pair(t, t)
        np.testing.assert_allclose(iou, 1.0, atol=1e-4)

    def test_non_overlapping_boxes(self):
        """Two boxes far apart should have IoU = 0."""
        c1 = _boxes_to_corners([(0, 0, 0)], [(1, 1, 1)], [(1, 0, 0, 0)])
        c2 = _boxes_to_corners([(100, 100, 100)], [(1, 1, 1)], [(1, 0, 0, 0)])
        iou = _compute_iou_3d_pair(
            torch.from_numpy(c1).float(),
            torch.from_numpy(c2).float(),
        )
        np.testing.assert_allclose(iou, 0.0, atol=1e-6)

    def test_partial_overlap(self):
        """Two overlapping boxes should have 0 < IoU < 1."""
        c1 = _boxes_to_corners([(0, 0, 0)], [(2, 2, 2)], [(1, 0, 0, 0)])
        c2 = _boxes_to_corners([(1, 0, 0)], [(2, 2, 2)], [(1, 0, 0, 0)])
        iou = _compute_iou_3d_pair(
            torch.from_numpy(c1).float(),
            torch.from_numpy(c2).float(),
        )
        assert 0.0 < iou < 1.0
        # Analytical: intersection volume = 1*2*2 = 4, union = 2*8 - 4 = 12
        np.testing.assert_allclose(iou, 4.0 / 12.0, atol=1e-3)

    def test_45_degree_self_iou(self):
        """A box rotated 45° compared to itself should have IoU = 1."""
        q = Quaternion(axis=[0, 0, 1], angle=math.pi / 4)
        rot = (q.w, q.x, q.y, q.z)
        c = _boxes_to_corners([(0, 0, 0)], [(3, 1, 2)], [rot])
        t = torch.from_numpy(c).float()
        iou = _compute_iou_3d_pair(t, t)
        np.testing.assert_allclose(iou, 1.0, atol=1e-4)

    def test_45_degree_vs_axis_aligned(self):
        """A 45° rotated box overlapping an axis-aligned box should give 0 < IoU < 1."""
        q = Quaternion(axis=[0, 0, 1], angle=math.pi / 4)
        rot = (q.w, q.x, q.y, q.z)
        c1 = _boxes_to_corners([(0, 0, 0)], [(2, 2, 2)], [rot])
        c2 = _boxes_to_corners([(0, 0, 0)], [(2, 2, 2)], [(1, 0, 0, 0)])
        iou = _compute_iou_3d_pair(
            torch.from_numpy(c1).float(),
            torch.from_numpy(c2).float(),
        )
        # Same center, same size; rotation reduces overlap
        assert 0.5 < iou < 1.0

    def test_45_degree_offset_overlap(self):
        """A 45° rotated box slightly offset from an axis-aligned box."""
        q = Quaternion(axis=[0, 0, 1], angle=math.pi / 4)
        rot = (q.w, q.x, q.y, q.z)
        c1 = _boxes_to_corners([(0, 0, 0)], [(4, 2, 2)], [rot])
        c2 = _boxes_to_corners([(1, 1, 0)], [(2, 2, 2)], [(1, 0, 0, 0)])
        iou = _compute_iou_3d_pair(
            torch.from_numpy(c1).float(),
            torch.from_numpy(c2).float(),
        )
        assert 0.0 < iou < 1.0


# ===================================================================
# 3D IoU — EvalBox-level API
# ===================================================================
class TestIoU3D:
    def test_identical_boxes_distance_zero(self):
        """iou_3d returns 1 - IoU; identical boxes → distance 0."""
        box = _make_box([0, 0, 0], [2, 2, 2])
        dist = iou_3d(box, box)
        np.testing.assert_allclose(dist, 0.0, atol=1e-4)

    def test_non_overlapping_distance_one(self):
        """Non-overlapping boxes → distance 1."""
        a = _make_box([0, 0, 0], [1, 1, 1])
        b = _make_box([50, 50, 50], [1, 1, 1])
        dist = iou_3d(a, b)
        np.testing.assert_allclose(dist, 1.0, atol=1e-4)

    def test_rotated_box_symmetry(self):
        """iou_3d(a, b) == iou_3d(b, a)."""
        q = Quaternion(axis=[0, 0, 1], angle=math.pi / 4)
        rot = (q.w, q.x, q.y, q.z)
        a = _make_box([0, 0, 0], [3, 1, 1], rot)
        b = _make_box([1, 0, 0], [2, 2, 2])
        np.testing.assert_allclose(iou_3d(a, b), iou_3d(b, a), atol=1e-6)

    def test_45_degree_self_distance_zero(self):
        """A 45° rotated box compared to itself should have distance 0."""
        q = Quaternion(axis=[0, 0, 1], angle=math.pi / 4)
        rot = (q.w, q.x, q.y, q.z)
        box = _make_box([5, 5, 5], [3, 1, 2], rot)
        np.testing.assert_allclose(iou_3d(box, box), 0.0, atol=1e-4)

    def test_45_degree_same_center_reduced_overlap(self):
        """Two boxes at the same center, one rotated 45°, should have distance > 0."""
        q = Quaternion(axis=[0, 0, 1], angle=math.pi / 4)
        rot = (q.w, q.x, q.y, q.z)
        a = _make_box([0, 0, 0], [4, 2, 2])
        b = _make_box([0, 0, 0], [4, 2, 2], rot)
        dist = iou_3d(a, b)
        assert 0.0 < dist < 1.0

    def test_45_degree_combined_rotation_axes(self):
        """Rotation around a diagonal axis (combined yaw+pitch effect) reduces overlap."""
        q = Quaternion(axis=[1, 1, 0], angle=math.pi / 4)
        rot = (q.w, q.x, q.y, q.z)
        a = _make_box([0, 0, 0], [3, 3, 3], rot)
        b = _make_box([0, 0, 0], [3, 3, 3])
        dist = iou_3d(a, b)
        # 45° around an edge-midpoint axis is not a cube symmetry, so IoU < 1
        assert 0.0 < dist < 1.0

    def test_cube_90_degree_face_normal_rotation(self):
        """A cube rotated 90° around a face normal maps onto itself → IoU = 1."""
        q = Quaternion(axis=[0, 0, 1], angle=math.pi / 2)
        rot = (q.w, q.x, q.y, q.z)
        a = _make_box([0, 0, 0], [3, 3, 3], rot)
        b = _make_box([0, 0, 0], [3, 3, 3])
        np.testing.assert_allclose(iou_3d(a, b), 0.0, atol=1e-3)


class TestIoU3DMatrix:
    def test_identity_diagonal(self):
        """Diagonal of the distance matrix for self-comparison should be 0."""
        boxes = [_make_box([i * 10, 0, 0], [2, 2, 2]) for i in range(3)]
        mat = iou_3d_matrix(boxes, boxes)
        assert mat.shape == (3, 3)
        np.testing.assert_allclose(np.diag(mat), 0.0, atol=1e-4)

    def test_symmetry(self):
        """D(gt, pred) should be close to D(pred, gt).T."""
        gt = [_make_box([0, 0, 0], [2, 2, 2]), _make_box([5, 0, 0], [1, 1, 1])]
        pred = [_make_box([1, 0, 0], [2, 2, 2])]
        d1 = iou_3d_matrix(gt, pred)
        d2 = iou_3d_matrix(pred, gt)
        np.testing.assert_allclose(d1, d2.T, atol=1e-6)

    def test_empty_gt(self):
        """Empty GT list should return (0, N) matrix of ones."""
        pred = [_make_box([0, 0, 0], [1, 1, 1])]
        mat = iou_3d_matrix([], pred)
        assert mat.shape == (0, 1)

    def test_empty_pred(self):
        """Empty pred list should return (M, 0) matrix of ones."""
        gt = [_make_box([0, 0, 0], [1, 1, 1])]
        mat = iou_3d_matrix(gt, [])
        assert mat.shape == (1, 0)

    def test_values_bounded(self):
        """All distances should be in [0, 1]."""
        boxes = [_make_box([i, 0, 0], [2, 2, 2]) for i in range(4)]
        mat = iou_3d_matrix(boxes, boxes)
        assert np.all(mat >= -1e-6)
        assert np.all(mat <= 1.0 + 1e-6)

    def test_45_degree_rotated_matrix(self):
        """Matrix with 45° rotated boxes should have correct diagonal and off-diagonal values."""
        q = Quaternion(axis=[0, 0, 1], angle=math.pi / 4)
        rot = (q.w, q.x, q.y, q.z)
        boxes = [
            _make_box([0, 0, 0], [4, 2, 2], rot),
            _make_box([0, 0, 0], [4, 2, 2]),
            _make_box([20, 0, 0], [1, 1, 1]),
        ]
        mat = iou_3d_matrix(boxes, boxes)
        assert mat.shape == (3, 3)
        # Self-comparison → 0 distance
        np.testing.assert_allclose(np.diag(mat), 0.0, atol=1e-4)
        # Rotated box at same center vs axis-aligned → partial overlap
        assert 0.0 < mat[0, 1] < 1.0
        # Far-away box → distance ~1
        np.testing.assert_allclose(mat[0, 2], 1.0, atol=1e-4)


# ===================================================================
# MTMC 3D BBox IoU (Euler-angle based, _BaseDataset static method)
# ===================================================================
class TestCalculate3DBBoxIoUs:
    @staticmethod
    def _make_boxes_array(boxes_list):
        """Convert list of [x, y, z, w, l, h, pitch, roll, yaw] to numpy array."""
        return np.array(boxes_list, dtype=np.float64)

    def test_identical_boxes(self):
        b = self._make_boxes_array([[0, 0, 0, 2, 2, 2, 0, 0, 0]])
        ious = _BaseDataset._calculate_3DBBox_ious(b, b)
        np.testing.assert_allclose(ious[0, 0], 1.0, atol=1e-4)

    def test_non_overlapping(self):
        b1 = self._make_boxes_array([[0, 0, 0, 1, 1, 1, 0, 0, 0]])
        b2 = self._make_boxes_array([[100, 100, 100, 1, 1, 1, 0, 0, 0]])
        ious = _BaseDataset._calculate_3DBBox_ious(b1, b2)
        np.testing.assert_allclose(ious[0, 0], 0.0, atol=1e-6)

    def test_partial_overlap_axis_aligned(self):
        b1 = self._make_boxes_array([[0, 0, 0, 2, 2, 2, 0, 0, 0]])
        b2 = self._make_boxes_array([[1, 0, 0, 2, 2, 2, 0, 0, 0]])
        ious = _BaseDataset._calculate_3DBBox_ious(b1, b2)
        expected_iou = 4.0 / 12.0  # intersection=1*2*2=4, union=2*8-4=12
        np.testing.assert_allclose(ious[0, 0], expected_iou, atol=1e-3)

    def test_yaw_rotation_90(self):
        """A 2x4x2 box rotated 90° around Z vs. an axis-aligned 4x2x2 box should match."""
        b1 = self._make_boxes_array([[0, 0, 0, 2, 4, 2, 0, 0, math.pi / 2]])
        b2 = self._make_boxes_array([[0, 0, 0, 4, 2, 2, 0, 0, 0]])
        ious = _BaseDataset._calculate_3DBBox_ious(b1, b2)
        np.testing.assert_allclose(ious[0, 0], 1.0, atol=1e-3)

    def test_yaw_rotation_45_self(self):
        """A box rotated 45° around Z should have IoU=1 with itself."""
        b = self._make_boxes_array([[0, 0, 0, 3, 1, 2, 0, 0, math.pi / 4]])
        ious = _BaseDataset._calculate_3DBBox_ious(b, b)
        np.testing.assert_allclose(ious[0, 0], 1.0, atol=1e-4)

    def test_yaw_rotation_45_vs_axis_aligned(self):
        """A 45° yaw-rotated box at the same center as an axis-aligned box of the same size."""
        b1 = self._make_boxes_array([[0, 0, 0, 2, 2, 2, 0, 0, math.pi / 4]])
        b2 = self._make_boxes_array([[0, 0, 0, 2, 2, 2, 0, 0, 0]])
        ious = _BaseDataset._calculate_3DBBox_ious(b1, b2)
        # Same center, same-sized square cross-section: 45° rotation reduces overlap
        assert 0.5 < ious[0, 0] < 1.0

    def test_pitch_rotation_45(self):
        """A 45° pitch rotation of a box at the same center."""
        b1 = self._make_boxes_array([[0, 0, 0, 2, 4, 2, math.pi / 4, 0, 0]])
        b2 = self._make_boxes_array([[0, 0, 0, 2, 4, 2, 0, 0, 0]])
        ious = _BaseDataset._calculate_3DBBox_ious(b1, b2)
        assert 0.0 < ious[0, 0] < 1.0

    def test_roll_rotation_45(self):
        """A 45° roll rotation of a box at the same center."""
        b1 = self._make_boxes_array([[0, 0, 0, 4, 2, 2, 0, math.pi / 4, 0]])
        b2 = self._make_boxes_array([[0, 0, 0, 4, 2, 2, 0, 0, 0]])
        ious = _BaseDataset._calculate_3DBBox_ious(b1, b2)
        assert 0.0 < ious[0, 0] < 1.0

    def test_combined_45_degree_rotations(self):
        """Non-trivial rotation with pitch=45°, yaw=45° at the same center."""
        b1 = self._make_boxes_array([[0, 0, 0, 3, 2, 1, math.pi / 4, 0, math.pi / 4]])
        b2 = self._make_boxes_array([[0, 0, 0, 3, 2, 1, 0, 0, 0]])
        ious = _BaseDataset._calculate_3DBBox_ious(b1, b2)
        assert 0.0 < ious[0, 0] < 1.0

    def test_contained_box(self):
        """A small box fully inside a larger box: IoU = small_vol / big_vol."""
        big = self._make_boxes_array([[0, 0, 0, 4, 4, 4, 0, 0, 0]])
        small = self._make_boxes_array([[0, 0, 0, 1, 1, 1, 0, 0, 0]])
        ious = _BaseDataset._calculate_3DBBox_ious(big, small)
        np.testing.assert_allclose(ious[0, 0], 1.0 / 64.0, atol=1e-2)

    def test_yaw_rotation_90_square_box(self):
        """A square-cross-section box rotated 90° around Z is identical to itself."""
        b1 = self._make_boxes_array([[0, 0, 0, 2, 2, 2, 0, 0, 0]])
        b2 = self._make_boxes_array([[0, 0, 0, 2, 2, 2, 0, 0, math.pi / 2]])
        ious = _BaseDataset._calculate_3DBBox_ious(b1, b2)
        np.testing.assert_allclose(ious[0, 0], 1.0, atol=1e-2)

    def test_batch_computation(self):
        """Multiple GT vs multiple pred boxes."""
        gt = self._make_boxes_array([
            [0, 0, 0, 2, 2, 2, 0, 0, 0],
            [10, 10, 10, 2, 2, 2, 0, 0, 0],
        ])
        pred = self._make_boxes_array([
            [0, 0, 0, 2, 2, 2, 0, 0, 0],
            [5, 5, 5, 1, 1, 1, 0, 0, 0],
            [10, 10, 10, 2, 2, 2, 0, 0, 0],
        ])
        ious = _BaseDataset._calculate_3DBBox_ious(gt, pred)
        assert ious.shape == (2, 3)
        np.testing.assert_allclose(ious[0, 0], 1.0, atol=1e-4)
        np.testing.assert_allclose(ious[1, 2], 1.0, atol=1e-4)
        np.testing.assert_allclose(ious[0, 1], 0.0, atol=1e-4)

    def test_batch_square_matrix(self):
        """Same-sized GT and pred lists with well-separated boxes → diagonal IoU=1, off-diagonal=0."""
        boxes_a = self._make_boxes_array([
            [0, 0, 0, 2, 2, 2, 0, 0, 0],
            [50, 50, 50, 2, 2, 2, 0, 0, 0],
        ])
        boxes_b = self._make_boxes_array([
            [0, 0, 0, 2, 2, 2, 0, 0, 0],
            [50, 50, 50, 2, 2, 2, 0, 0, 0],
        ])
        ious = _BaseDataset._calculate_3DBBox_ious(boxes_a, boxes_b)
        assert ious.shape == (2, 2)
        np.testing.assert_allclose(ious[0, 0], 1.0, atol=1e-4)
        np.testing.assert_allclose(ious[1, 1], 1.0, atol=1e-4)
        np.testing.assert_allclose(ious[0, 1], 0.0, atol=1e-4)
        np.testing.assert_allclose(ious[1, 0], 0.0, atol=1e-4)

    def test_empty_inputs(self):
        b1 = self._make_boxes_array([]).reshape(0, 9)
        b2 = self._make_boxes_array([[0, 0, 0, 1, 1, 1, 0, 0, 0]])
        ious = _BaseDataset._calculate_3DBBox_ious(b1, b2)
        assert ious.shape == (0, 1)

    def test_values_bounded(self):
        """IoU values from _calculate_3DBBox_ious should be in [0, 1]."""
        boxes = self._make_boxes_array([
            [0, 0, 0, 2, 3, 4, 0.1, 0.2, 0.3],
            [1, 1, 1, 3, 2, 1, 0.5, 0, 0.8],
            [0.5, 0.5, 0.5, 1, 1, 1, 0, 0, 0],
        ])
        ious = _BaseDataset._calculate_3DBBox_ious(boxes, boxes)
        assert np.all(ious >= -1e-6)
        assert np.all(ious <= 1.0 + 1e-6)


# ===================================================================
# 2D Box IoU (_BaseDataset._calculate_box_ious, xywh format)
# ===================================================================
class TestCalculateBoxIoUs:
    def test_identical_boxes(self):
        """Identical 2D boxes should have IoU = 1."""
        a = np.array([[0, 0, 4, 4]], dtype=np.float64)
        iou = _BaseDataset._calculate_box_ious(a, a.copy())
        np.testing.assert_allclose(iou, [[1.0]], atol=1e-6)

    def test_no_overlap(self):
        """Two separated 2D boxes should have IoU = 0."""
        a = np.array([[0, 0, 1, 1]], dtype=np.float64)
        b = np.array([[10, 10, 1, 1]], dtype=np.float64)
        iou = _BaseDataset._calculate_box_ious(a, b)
        np.testing.assert_allclose(iou, [[0.0]], atol=1e-6)

    def test_partial_overlap(self):
        """Two partially overlapping 2D boxes."""
        a = np.array([[0, 0, 4, 4]], dtype=np.float64)
        b = np.array([[2, 2, 4, 4]], dtype=np.float64)
        iou = _BaseDataset._calculate_box_ious(a, b)
        # intersection = 2*2 = 4, union = 16 + 16 - 4 = 28
        np.testing.assert_allclose(iou, [[4.0 / 28.0]], atol=1e-4)

    def test_contained_box(self):
        """A small box inside a larger one."""
        big = np.array([[0, 0, 10, 10]], dtype=np.float64)
        small = np.array([[2, 2, 2, 2]], dtype=np.float64)
        iou = _BaseDataset._calculate_box_ious(big, small)
        # intersection = 2*2 = 4, union = 100 + 4 - 4 = 100
        np.testing.assert_allclose(iou, [[4.0 / 100.0]], atol=1e-4)

    def test_multiple_boxes(self):
        """Batch 2D IoU computation."""
        a = np.array([[0, 0, 2, 2], [10, 10, 2, 2]], dtype=np.float64)
        b = np.array([[0, 0, 2, 2], [10, 10, 2, 2]], dtype=np.float64)
        iou = _BaseDataset._calculate_box_ious(a, b)
        assert iou.shape == (2, 2)
        np.testing.assert_allclose(iou[0, 0], 1.0, atol=1e-6)
        np.testing.assert_allclose(iou[1, 1], 1.0, atol=1e-6)
        np.testing.assert_allclose(iou[0, 1], 0.0, atol=1e-6)

    def test_ioa_mode(self):
        """Intersection-over-area mode for crowd ignore regions."""
        big = np.array([[0, 0, 10, 10]], dtype=np.float64)
        small = np.array([[2, 2, 2, 2]], dtype=np.float64)
        ioa = _BaseDataset._calculate_box_ious(big, small, do_ioa=True)
        # IoA = intersection / area(big) = 4 / 100
        np.testing.assert_allclose(ioa, [[4.0 / 100.0]], atol=1e-4)

    def test_x0y0x1y1_format(self):
        """IoU in x0y0x1y1 format."""
        a = np.array([[0, 0, 4, 4]], dtype=np.float64)
        b = np.array([[2, 2, 6, 6]], dtype=np.float64)
        iou = _BaseDataset._calculate_box_ious(a, b, box_format="x0y0x1y1")
        # intersection = 2*2 = 4, union = 16 + 16 - 4 = 28
        np.testing.assert_allclose(iou, [[4.0 / 28.0]], atol=1e-4)


# ===================================================================
# HOTA metric evaluation
# ===================================================================
def _build_hota_sequence_data(
    num_timesteps,
    gt_ids_per_t,
    tracker_ids_per_t,
    similarity_scores_per_t,
):
    """
    Build the data dict expected by HOTA.eval_sequence.

    Each argument that is "per_t" is a list of length num_timesteps.
    gt_ids_per_t[t] and tracker_ids_per_t[t] are 1D int arrays.
    similarity_scores_per_t[t] is a 2D float array of shape (len(gt), len(tracker)).
    """
    all_gt_ids = set()
    all_tracker_ids = set()
    num_gt_dets = 0
    num_tracker_dets = 0

    for gt_ids, tr_ids in zip(gt_ids_per_t, tracker_ids_per_t):
        all_gt_ids.update(gt_ids.tolist())
        all_tracker_ids.update(tr_ids.tolist())
        num_gt_dets += len(gt_ids)
        num_tracker_dets += len(tr_ids)

    gt_id_map = {v: i for i, v in enumerate(sorted(all_gt_ids))}
    tr_id_map = {v: i for i, v in enumerate(sorted(all_tracker_ids))}

    mapped_gt = [np.array([gt_id_map[x] for x in ids]) for ids in gt_ids_per_t]
    mapped_tr = [np.array([tr_id_map[x] for x in ids]) for ids in tracker_ids_per_t]

    return {
        "num_timesteps": num_timesteps,
        "num_gt_ids": len(all_gt_ids),
        "num_tracker_ids": len(all_tracker_ids),
        "num_gt_dets": num_gt_dets,
        "num_tracker_dets": num_tracker_dets,
        "gt_ids": mapped_gt,
        "tracker_ids": mapped_tr,
        "similarity_scores": similarity_scores_per_t,
        "seq": "test_seq",
    }


class TestHOTA:
    def setup_method(self):
        self.hota = HOTA()

    def test_perfect_tracking(self):
        """
        Perfect 1-to-1 matching with IoU=1 at every frame.
        All alpha thresholds should be fully satisfied.
        """
        T = 5
        data = _build_hota_sequence_data(
            num_timesteps=T,
            gt_ids_per_t=[np.array([0, 1])] * T,
            tracker_ids_per_t=[np.array([0, 1])] * T,
            similarity_scores_per_t=[np.eye(2)] * T,
        )
        res = self.hota.eval_sequence(data)

        np.testing.assert_allclose(res["HOTA_TP"], T * 2, atol=1e-6)
        np.testing.assert_allclose(res["HOTA_FN"], 0.0, atol=1e-6)
        np.testing.assert_allclose(res["HOTA_FP"], 0.0, atol=1e-6)
        np.testing.assert_allclose(res["DetA"], 1.0, atol=1e-6)
        np.testing.assert_allclose(res["AssA"], 1.0, atol=1e-6)
        np.testing.assert_allclose(res["HOTA"], 1.0, atol=1e-6)
        np.testing.assert_allclose(res["LocA"], 1.0, atol=1e-6)

    def test_no_predictions(self):
        """No tracker detections → all GT become FN, zero TP/FP."""
        T = 3
        data = _build_hota_sequence_data(
            num_timesteps=T,
            gt_ids_per_t=[np.array([0, 1])] * T,
            tracker_ids_per_t=[np.array([], dtype=int)] * T,
            similarity_scores_per_t=[np.zeros((2, 0))] * T,
        )
        res = self.hota.eval_sequence(data)

        np.testing.assert_allclose(res["HOTA_FN"], T * 2, atol=1e-6)
        np.testing.assert_allclose(res["HOTA_TP"], 0.0, atol=1e-6)
        np.testing.assert_allclose(res["HOTA_FP"], 0.0, atol=1e-6)
        np.testing.assert_allclose(res["DetA"], 0.0, atol=1e-6)
        np.testing.assert_allclose(res["HOTA"], 0.0, atol=1e-6)

    def test_no_ground_truth(self):
        """No GT detections → all predictions become FP, zero TP/FN."""
        T = 3
        data = _build_hota_sequence_data(
            num_timesteps=T,
            gt_ids_per_t=[np.array([], dtype=int)] * T,
            tracker_ids_per_t=[np.array([0, 1])] * T,
            similarity_scores_per_t=[np.zeros((0, 2))] * T,
        )
        res = self.hota.eval_sequence(data)

        np.testing.assert_allclose(res["HOTA_FP"], T * 2, atol=1e-6)
        np.testing.assert_allclose(res["HOTA_TP"], 0.0, atol=1e-6)
        np.testing.assert_allclose(res["HOTA_FN"], 0.0, atol=1e-6)
        np.testing.assert_allclose(res["DetA"], 0.0, atol=1e-6)
        np.testing.assert_allclose(res["HOTA"], 0.0, atol=1e-6)

    def test_all_false_positives(self):
        """
        GT has one object, tracker produces a different ID with zero similarity.
        Everything should be FN + FP.
        """
        T = 2
        data = _build_hota_sequence_data(
            num_timesteps=T,
            gt_ids_per_t=[np.array([0])] * T,
            tracker_ids_per_t=[np.array([0])] * T,
            similarity_scores_per_t=[np.array([[0.0]])] * T,
        )
        res = self.hota.eval_sequence(data)
        # With similarity = 0, all alphas >= 0.05 reject the match
        np.testing.assert_allclose(res["HOTA_TP"], 0.0, atol=1e-6)
        np.testing.assert_allclose(res["HOTA_FN"], T, atol=1e-6)
        np.testing.assert_allclose(res["HOTA_FP"], T, atol=1e-6)

    def test_partial_similarity(self):
        """
        Similarity = 0.5 means matches are TP for alpha <= 0.5, FN/FP for alpha > 0.5.
        """
        T = 4
        data = _build_hota_sequence_data(
            num_timesteps=T,
            gt_ids_per_t=[np.array([0])] * T,
            tracker_ids_per_t=[np.array([0])] * T,
            similarity_scores_per_t=[np.array([[0.5]])] * T,
        )
        res = self.hota.eval_sequence(data)

        alpha_labels = self.hota.array_labels
        low_alpha_mask = alpha_labels <= 0.5 + np.finfo("float").eps
        high_alpha_mask = ~low_alpha_mask

        np.testing.assert_allclose(res["HOTA_TP"][low_alpha_mask], T, atol=1e-6)
        np.testing.assert_allclose(res["HOTA_FN"][low_alpha_mask], 0.0, atol=1e-6)
        np.testing.assert_allclose(res["HOTA_FP"][low_alpha_mask], 0.0, atol=1e-6)

        np.testing.assert_allclose(res["HOTA_TP"][high_alpha_mask], 0.0, atol=1e-6)
        np.testing.assert_allclose(res["HOTA_FN"][high_alpha_mask], T, atol=1e-6)
        np.testing.assert_allclose(res["HOTA_FP"][high_alpha_mask], T, atol=1e-6)

    def test_id_switch(self):
        """
        Two GT objects, tracker swaps IDs midway through.
        Association should be imperfect even though detection is perfect.
        """
        T = 4
        gt_ids = [np.array([0, 1])] * T
        # Tracker matches correctly for first half, then swaps
        tracker_ids = [np.array([0, 1])] * 2 + [np.array([1, 0])] * 2
        sim = [np.eye(2)] * T

        data = _build_hota_sequence_data(
            num_timesteps=T,
            gt_ids_per_t=gt_ids,
            tracker_ids_per_t=tracker_ids,
            similarity_scores_per_t=sim,
        )
        res = self.hota.eval_sequence(data)

        # Detection should be perfect
        np.testing.assert_allclose(res["HOTA_TP"], T * 2, atol=1e-6)
        np.testing.assert_allclose(res["DetA"], 1.0, atol=1e-6)
        # Association should be imperfect due to the ID switch
        assert np.all(res["AssA"] < 1.0)
        # HOTA = sqrt(DetA * AssA) < 1
        assert np.all(res["HOTA"] < 1.0)

    def test_combine_sequences(self):
        """combine_sequences should correctly aggregate TP/FN/FP."""
        T = 3
        data1 = _build_hota_sequence_data(
            num_timesteps=T,
            gt_ids_per_t=[np.array([0])] * T,
            tracker_ids_per_t=[np.array([0])] * T,
            similarity_scores_per_t=[np.array([[1.0]])] * T,
        )
        data2 = _build_hota_sequence_data(
            num_timesteps=T,
            gt_ids_per_t=[np.array([0])] * T,
            tracker_ids_per_t=[np.array([0])] * T,
            similarity_scores_per_t=[np.array([[1.0]])] * T,
        )
        res1 = self.hota.eval_sequence(data1)
        res2 = self.hota.eval_sequence(data2)

        combined = self.hota.combine_sequences({"seq1": res1, "seq2": res2})

        np.testing.assert_allclose(combined["HOTA_TP"], res1["HOTA_TP"] + res2["HOTA_TP"], atol=1e-6)
        np.testing.assert_allclose(combined["HOTA_FN"], res1["HOTA_FN"] + res2["HOTA_FN"], atol=1e-6)
        np.testing.assert_allclose(combined["HOTA_FP"], res1["HOTA_FP"] + res2["HOTA_FP"], atol=1e-6)

    def test_compute_final_fields(self):
        """_compute_final_fields should derive DetA, DetRe, DetPr, HOTA from raw counts."""
        res = {
            "HOTA_TP": np.array([10.0, 5.0]),
            "HOTA_FN": np.array([2.0, 5.0]),
            "HOTA_FP": np.array([3.0, 5.0]),
            "AssA": np.array([0.8, 0.6]),
            "AssRe": np.array([0.9, 0.7]),
            "AssPr": np.array([0.85, 0.65]),
            "LocA": np.array([0.9, 0.8]),
            "HOTA": np.zeros(2),
            "DetA": np.zeros(2),
            "DetRe": np.zeros(2),
            "DetPr": np.zeros(2),
            "OWTA": np.zeros(2),
            "HOTA(0)": 0.0,
            "LocA(0)": 0.0,
            "HOTALocA(0)": 0.0,
        }
        out = HOTA._compute_final_fields(res)

        expected_det_re = np.array([10 / 12, 5 / 10])
        expected_det_pr = np.array([10 / 13, 5 / 10])
        expected_det_a = np.array([10 / 15, 5 / 15])
        expected_hota = np.sqrt(expected_det_a * np.array([0.8, 0.6]))

        np.testing.assert_allclose(out["DetRe"], expected_det_re, atol=1e-6)
        np.testing.assert_allclose(out["DetPr"], expected_det_pr, atol=1e-6)
        np.testing.assert_allclose(out["DetA"], expected_det_a, atol=1e-6)
        np.testing.assert_allclose(out["HOTA"], expected_hota, atol=1e-6)
        np.testing.assert_allclose(out["HOTA(0)"], expected_hota[0], atol=1e-6)
        np.testing.assert_allclose(out["LocA(0)"], 0.9, atol=1e-6)
        np.testing.assert_allclose(out["HOTALocA(0)"], expected_hota[0] * 0.9, atol=1e-6)

    def test_hota_output_fields(self):
        """eval_sequence result should contain all expected HOTA fields."""
        T = 2
        data = _build_hota_sequence_data(
            num_timesteps=T,
            gt_ids_per_t=[np.array([0])] * T,
            tracker_ids_per_t=[np.array([0])] * T,
            similarity_scores_per_t=[np.array([[0.8]])] * T,
        )
        res = self.hota.eval_sequence(data)

        for field in self.hota.fields:
            assert field in res, f"Missing field: {field}"

    def test_array_labels_range(self):
        """Alpha thresholds should span from ~0.05 to ~0.95."""
        labels = self.hota.array_labels
        assert len(labels) > 0
        np.testing.assert_allclose(labels[0], 0.05, atol=1e-6)
        assert labels[-1] < 1.0


# ===================================================================
# Consistency: common/utils 3D IoU vs. MTMC _calculate_3DBBox_ious
# ===================================================================
class TestIoUConsistency:
    """Verify both IoU implementations agree for axis-aligned boxes."""

    def test_identical_axis_aligned(self):
        box_eval = _make_box([0, 0, 0], [2, 3, 4])
        dist = iou_3d(box_eval, box_eval)

        bbox_arr = np.array([[0, 0, 0, 2, 3, 4, 0, 0, 0]], dtype=np.float64)
        mtmc_iou = _BaseDataset._calculate_3DBBox_ious(bbox_arr, bbox_arr)

        np.testing.assert_allclose(1.0 - dist, mtmc_iou[0, 0], atol=1e-3)

    def test_separated_axis_aligned(self):
        a = _make_box([0, 0, 0], [1, 1, 1])
        b = _make_box([10, 10, 10], [1, 1, 1])
        dist = iou_3d(a, b)

        arr_a = np.array([[0, 0, 0, 1, 1, 1, 0, 0, 0]], dtype=np.float64)
        arr_b = np.array([[10, 10, 10, 1, 1, 1, 0, 0, 0]], dtype=np.float64)
        mtmc_iou = _BaseDataset._calculate_3DBBox_ious(arr_a, arr_b)

        np.testing.assert_allclose(1.0 - dist, mtmc_iou[0, 0], atol=1e-6)

    def test_partial_overlap_axis_aligned(self):
        a = _make_box([0, 0, 0], [2, 2, 2])
        b = _make_box([1, 0, 0], [2, 2, 2])
        dist = iou_3d(a, b)

        arr_a = np.array([[0, 0, 0, 2, 2, 2, 0, 0, 0]], dtype=np.float64)
        arr_b = np.array([[1, 0, 0, 2, 2, 2, 0, 0, 0]], dtype=np.float64)
        mtmc_iou = _BaseDataset._calculate_3DBBox_ious(arr_a, arr_b)

        np.testing.assert_allclose(1.0 - dist, mtmc_iou[0, 0], atol=1e-3)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

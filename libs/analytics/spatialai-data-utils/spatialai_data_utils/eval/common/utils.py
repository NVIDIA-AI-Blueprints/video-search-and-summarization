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
3D IoU computation utilities for detection and tracking evaluation.

Uses the same approach as mtmc_validation_module: converts oriented bounding boxes
to 8 corner points using full 3D rotation (pitch, roll, yaw), then computes 3D IoU
using pytorch3d.ops.box3d_overlap. Handles arbitrary 3D rotations, not just yaw.
"""

from typing import TYPE_CHECKING, List

import numpy as np
from pyquaternion import Quaternion

from nuscenes.eval.common.data_classes import EvalBox
from spatialai_data_utils.utils.optional_dependencies import (
    import_box3d_overlap,
    import_torch,
)

# torch/pytorch3d are optional runtime deps; imported locally inside IoU fns.
if TYPE_CHECKING:
    import torch


def _quaternion_to_rotation_matrix(q: Quaternion) -> np.ndarray:
    """
    Convert a quaternion to a 3x3 rotation matrix.

    :param q: Quaternion instance.
    :return: (3, 3) rotation matrix.
    """
    return q.rotation_matrix


def _boxes_to_corners(translations, sizes, rotations):
    """
    Convert a list of boxes (translation, size, rotation quaternion) to 8 corner points.
    Uses full 3D rotation from quaternion, same as mtmc_validation_module.

    :param translations: List of (x, y, z) tuples.
    :param sizes: List of (w, l, h) tuples.
    :param rotations: List of quaternion tuples (w, x, y, z).
    :return: (N, 8, 3) numpy array of corner coordinates.
    """
    N = len(translations)
    unit_corners = np.array([
        [0, 0, 0],
        [1, 0, 0],
        [1, 1, 0],
        [0, 1, 0],
        [0, 0, 1],
        [1, 0, 1],
        [1, 1, 1],
        [0, 1, 1],
    ], dtype=np.float64)

    corners_out = np.zeros((N, 8, 3), dtype=np.float64)

    for i in range(N):
        x, y, z = translations[i][0], translations[i][1], translations[i][2]
        width, length, height = sizes[i][0], sizes[i][1], sizes[i][2]

        local_corners = unit_corners.copy()
        local_corners[:, 0] *= width
        local_corners[:, 1] *= length
        local_corners[:, 2] *= height

        # Shift so center is at origin
        local_corners[:, 0] -= width / 2.0
        local_corners[:, 1] -= length / 2.0
        local_corners[:, 2] -= height / 2.0

        # Full 3D rotation from quaternion
        R = _quaternion_to_rotation_matrix(Quaternion(rotations[i]))
        local_corners = local_corners @ R.T  # (8, 3)

        # Translate to world coords
        local_corners[:, 0] += x
        local_corners[:, 1] += y
        local_corners[:, 2] += z

        corners_out[i] = local_corners

    return corners_out


def _compute_iou_3d_pair(gt_corners: "torch.Tensor", pred_corners: "torch.Tensor") -> float:
    """
    Compute 3D IoU between two single boxes given their corners.
    Uses pytorch3d.ops.box3d_overlap.

    :param gt_corners: (1, 8, 3) tensor of GT box corners.
    :param pred_corners: (1, 8, 3) tensor of predicted box corners.
    :return: IoU value.
    """
    box3d_overlap = import_box3d_overlap("3D IoU computation")

    _, iou = box3d_overlap(gt_corners, pred_corners)
    return float(iou[0, 0].item())


def iou_3d(gt_box: EvalBox, pred_box: EvalBox) -> float:
    """
    Compute 3D IoU distance between two EvalBox instances.
    Uses the same corner-based approach as mtmc_validation_module with
    pytorch3d.ops.box3d_overlap. Handles full 3D rotation (pitch, roll, yaw).

    Returns 1 - IoU so that the result is a distance metric (lower is better),
    compatible with the existing center_distance-based matching logic.

    :param gt_box: Ground truth box with translation, size, rotation attributes.
    :param pred_box: Predicted box with translation, size, rotation attributes.
    :return: 1.0 - iou_3d. Range [0, 1], where 0 means perfect overlap.
    """
    torch = import_torch("3D IoU computation")

    corners = _boxes_to_corners(
        [gt_box.translation, pred_box.translation],
        [gt_box.size, pred_box.size],
        [gt_box.rotation, pred_box.rotation],
    )

    gt_corners = torch.from_numpy(corners[0:1]).float()
    pred_corners = torch.from_numpy(corners[1:2]).float()

    iou = _compute_iou_3d_pair(gt_corners, pred_corners)
    return 1.0 - iou


def iou_3d_matrix(gt_boxes: List[EvalBox], pred_boxes: List[EvalBox]) -> np.ndarray:
    """
    Compute pairwise 3D IoU distance matrix between lists of GT and predicted boxes.
    Uses pytorch3d.ops.box3d_overlap for the computation, same as mtmc_validation_module.
    Handles full 3D rotation (pitch, roll, yaw).

    Returns a (M, N) matrix where entry [i, j] = 1 - IoU_3D(gt_boxes[i], pred_boxes[j]).
    This is the vectorized version used by the tracking evaluation.

    :param gt_boxes: List of M ground truth EvalBox instances.
    :param pred_boxes: List of N predicted EvalBox instances.
    :return: (M, N) distance matrix.
    """
    M = len(gt_boxes)
    N = len(pred_boxes)

    if M == 0 or N == 0:
        return np.ones((M, N), dtype=np.float64)

    torch = import_torch("3D IoU computation")
    box3d_overlap = import_box3d_overlap("3D IoU computation")

    # Convert all boxes to corners
    gt_corners = _boxes_to_corners(
        [b.translation for b in gt_boxes],
        [b.size for b in gt_boxes],
        [b.rotation for b in gt_boxes],
    )
    pred_corners = _boxes_to_corners(
        [b.translation for b in pred_boxes],
        [b.size for b in pred_boxes],
        [b.rotation for b in pred_boxes],
    )

    # Use pytorch3d for batch IoU computation
    gt_corners_t = torch.from_numpy(gt_corners).float()    # (M, 8, 3)
    pred_corners_t = torch.from_numpy(pred_corners).float()  # (N, 8, 3)

    _, iou_matrix = box3d_overlap(gt_corners_t, pred_corners_t)  # (M, N)

    # Convert IoU to distance (1 - IoU)
    distances = 1.0 - iou_matrix.cpu().detach().numpy()

    return distances.astype(np.float64)

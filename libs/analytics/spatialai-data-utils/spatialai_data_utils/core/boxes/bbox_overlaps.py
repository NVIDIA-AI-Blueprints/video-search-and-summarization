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
Bounding Box Overlap Computation Module

This module provides efficient computation of overlaps between 2D bounding boxes.
It supports both IoU (Intersection over Union) and IoF (Intersection over Foreground)
metrics, commonly used in object detection and tracking algorithms.

Key Features:
- Compute IoU (Intersection over Union) between bounding box sets
- Compute IoF (Intersection over Foreground/Area) for one-sided overlap
- Efficient numpy-based implementation
- Handle empty bounding box sets gracefully
- Automatic optimization for asymmetric input sizes

Main Functions:
- bbox_overlaps: Compute pairwise overlaps between two sets of boxes

Overlap Metrics:

IoU (Intersection over Union):
- Symmetric metric: IoU(A, B) = Area(A ∩ B) / Area(A ∪ B)
- Range: [0, 1] where 1 means perfect overlap
- Used for: NMS, matching, detection evaluation

IoF (Intersection over Foreground):
- Asymmetric metric: IoF(A, B) = Area(A ∩ B) / Area(A)
- Measures how much of the first box overlaps with the second
- Used for: Tracking, association, occlusion handling

Use Cases:
- Non-Maximum Suppression (NMS) for detection post-processing
- Object tracking association (match detections to tracks)
- Evaluation metrics (compare predictions with ground truth)
- Data association in multi-object tracking
- Occlusion reasoning

Typical Usage:
1. Collect bounding boxes in [xmin, ymin, xmax, ymax] format
2. Call bbox_overlaps with two sets of boxes
3. Use resulting overlap matrix for matching or filtering
4. Apply threshold to determine positive matches

Performance:
- Optimized for batch processing
- Swaps input order for better cache efficiency
- Handles large numbers of boxes efficiently
"""

import numpy as np


def bbox_overlaps(bboxes1, bboxes2, mode="iou", eps=1e-6):
    """
    Calculate the overlaps between each bounding box in `bboxes1` and `bboxes2`.

    Supports Intersection over Union (IoU) and Intersection over Foreground (IoF) modes.

    :param bboxes1: A NumPy array of bounding boxes with shape (N, 4),
                    where each row represents [x_min, y_min, x_max, y_max].
    :type bboxes1: numpy.ndarray
    :param bboxes2: A NumPy array of bounding boxes with shape (K, 4).
    :type bboxes2: numpy.ndarray
    :param mode: The mode for calculating overlap. Options are 'iou' (Intersection over Union)
                 or 'iof' (Intersection over Foreground - area of overlap / area of `bboxes1`).
                 Defaults to 'iou'.
    :type mode: str, optional
    :param eps: A small epsilon value added to the denominator for numerical stability,
                preventing division by zero. Defaults to 1e-6.
    :type eps: float, optional
    :return: A NumPy array of shape (N, K) containing the overlap values (IoU or IoF)
             between the bounding boxes. `ious[i, j]` represents the overlap between
             `bboxes1[i]` and `bboxes2[j]`.
    :rtype: numpy.ndarray
    :raises AssertionError: If `mode` is not 'iou' or 'iof'.
    """

    assert mode in ["iou", "iof"]

    bboxes1 = bboxes1.astype(np.float32)
    bboxes2 = bboxes2.astype(np.float32)
    rows = bboxes1.shape[0]
    cols = bboxes2.shape[0]
    ious = np.zeros((rows, cols), dtype=np.float32)
    if rows * cols == 0:
        return ious
    exchange = False
    if bboxes1.shape[0] > bboxes2.shape[0]:
        bboxes1, bboxes2 = bboxes2, bboxes1
        ious = np.zeros((cols, rows), dtype=np.float32)
        exchange = True
    area1 = (bboxes1[:, 2] - bboxes1[:, 0]) * (bboxes1[:, 3] - bboxes1[:, 1])
    area2 = (bboxes2[:, 2] - bboxes2[:, 0]) * (bboxes2[:, 3] - bboxes2[:, 1])
    for i in range(bboxes1.shape[0]):
        x_start = np.maximum(bboxes1[i, 0], bboxes2[:, 0])
        y_start = np.maximum(bboxes1[i, 1], bboxes2[:, 1])
        x_end = np.minimum(bboxes1[i, 2], bboxes2[:, 2])
        y_end = np.minimum(bboxes1[i, 3], bboxes2[:, 3])
        overlap = np.maximum(x_end - x_start, 0) * np.maximum(y_end - y_start, 0)
        if mode == "iou":
            union = area1[i] + area2 - overlap
        else:
            union = area1[i] if not exchange else area2
        union = np.maximum(union, eps)
        ious[i, :] = overlap / union
    if exchange:
        ious = ious.T
    return ious

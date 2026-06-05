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
3D Bounding Box NMS (Non-Maximum Suppression) Module

This module provides Non-Maximum Suppression algorithms specifically designed
for 3D object detection in Bird's Eye View (BEV). It uses circular (radial)
distance-based suppression instead of traditional IoU-based NMS.

Key Features:
- Circular NMS using BEV distance metric
- Confidence-based suppression (keep highest confidence detections)
- Configurable distance threshold and maximum output size
- Efficient for BEV object detection post-processing

Main Functions:
- circle_nms: Apply circular NMS to 3D detections in BEV

Algorithm:
Instead of using 3D IoU, this NMS uses Euclidean distance between object centers
in the BEV plane. An object is suppressed if another object with higher confidence
exists within a specified radius.

Advantages:
- Faster than 3D IoU computation
- Simple distance metric appropriate for BEV
- Effective for objects with similar sizes
- Commonly used in BEV-based 3D detection models

Use Cases:
- Post-process BEVFormer, Sparse4D, or other BEV detector outputs
- Remove duplicate detections in multi-camera fusion
- Filter nearby detections with lower confidence
- Prepare detection results for tracking

Typical Usage:
1. Collect all 3D detections from model
2. Format as [x, y, confidence] array
3. Apply circle_nms with distance threshold
4. Keep returned indices for final detections
"""

import numpy as np


def circle_nms(dets, thresh, post_max_size=83):
    """
    Apply circular (radial) Non-Maximum Suppression to 3D detections in BEV.

    This NMS variant suppresses detections based on their 2D distance in the
    Bird's Eye View plane. An object is suppressed if another object with higher
    confidence exists within a specified distance threshold. Detections are
    processed in descending order of confidence.

    :param dets: Detection results array with shape [N, 3] where each row contains
                 [x_center, y_center, confidence_score]. x and y are in world
                 coordinates (typically meters), and confidence is the detection score.
    :type dets: numpy.ndarray
    :param thresh: Distance threshold (squared) for suppression. If the squared distance
                   between two detection centers is <= thresh, the one with lower
                   confidence is suppressed. Typical values: 1.0-4.0 (for 1-2m radius).
    :type thresh: float
    :param post_max_size: Maximum number of detections to keep after NMS. Only the top
                          post_max_size detections (by confidence) are returned.
                          Defaults to 83.
    :type post_max_size: int
    :return: List of indices of detections to keep, sorted by confidence (highest first).
             Length is at most post_max_size.
    :rtype: list of int

    Note:
        - The threshold is compared against squared distance for efficiency
        - To get radius r, use thresh = r²
        - Example: For 2 meter radius, use thresh = 4.0

    Example:
        >>> dets = np.array([
        ...     [10.0, 20.0, 0.9],  # High confidence
        ...     [10.5, 20.3, 0.8],  # Nearby with lower confidence (will be suppressed)
        ...     [30.0, 40.0, 0.85]  # Far away (will be kept)
        ... ])
        >>> keep_indices = circle_nms(dets, thresh=1.0, post_max_size=10)
        >>> filtered_dets = dets[keep_indices]
    """
    x1 = dets[:, 0]
    y1 = dets[:, 1]
    scores = dets[:, 2]
    order = scores.argsort()[::-1]  # highest->lowest
    ndets = dets.shape[0]
    suppressed = np.zeros((ndets))
    keep = []
    for _i in range(ndets):
        i = order[_i]  # start with highest score box
        if suppressed[i] == 1:  # if any box have enough iou with this, remove it
            continue
        keep.append(i)
        for _j in range(_i + 1, ndets):
            j = order[_j]
            if suppressed[j] == 1:
                continue
            # calculate center distance between i and j box
            dist = (x1[i] - x1[j]) ** 2 + (y1[i] - y1[j]) ** 2

            # ovr = inter / areas[j]
            if dist <= thresh:
                suppressed[j] = 1
    return keep[:post_max_size]
